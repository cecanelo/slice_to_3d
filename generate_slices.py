import argparse
import math
import numpy as np
from PIL import Image, ImageDraw
import os
import shutil
import json
import trimesh
import shapely.affinity


def parse_args():
    parser = argparse.ArgumentParser(description='Create a PNG from parametric shape')
    parser.add_argument('--pixel-size', type=float, default=0.05, help='mm/pixel')
    parser.add_argument('--layer-height', type=float, default=0.1, help='layer height in mm')
    parser.add_argument('--out-folder', required=True, help='output folder')

    subparsers = parser.add_subparsers(dest='shape', required=True)

    sphere_parser = subparsers.add_parser('sphere')
    sphere_parser.add_argument('--radius', type=float, required=True)

    cube_parser = subparsers.add_parser('cube')
    cube_parser.add_argument('--side', type=float, required=True)

    cone_parser = subparsers.add_parser('cone')
    cone_parser.add_argument('--radius', type=float, required=True, help='base radius')
    cone_parser.add_argument('--height', type=float, required=True)

    mesh_parser = subparsers.add_parser('from-mesh')
    mesh_parser.add_argument('--input', required=True, help='path to stl file')

    return parser.parse_args()

def sphere_radius_at(z, radius):
    z_centered = z - radius   # shift so the caller's z=0 is the bottom of the sphere
    if abs(z_centered) > radius:
        return None
    return math.sqrt(radius**2 - z_centered**2)

def cone_radius_at(z, radius, height):
    if z < 0 or z > height:
        return None
    return radius * (1 - z/height)

def cube_halfwidth_at(z, side):
    if z < 0 or z > side:
        return None
    return side / 2

def mesh_cross_section_at(mesh, z):
    section = mesh.section(plane_origin=[0,0,z], plane_normal=[0,0,1])
    if section is None:
        return None
    planar, transform = section.to_2D()

    # to_planar()'s coordinates are in a per-slice local 2D frame, not the
    # mesh's global X/Y - apply the returned transform to get back to
    # global coordinates so every layer lines up consistently.
    matrix = [
        transform[0, 0], transform[0, 1],
        transform[1, 0], transform[1, 1],
        transform[0, 3], transform[1, 3],
    ]

    polygons = []
    for poly in planar.polygons_full:
        polygons.append(shapely.affinity.affine_transform(poly, matrix))
    return polygons

def rasterize_circle(radius, img_width_px, img_height_px, pixel_size):
    yy, xx = np.mgrid[0:img_height_px, 0:img_width_px]
    x_mm = (xx - img_width_px / 2) * pixel_size
    y_mm = (yy - img_height_px / 2) * pixel_size
    return (x_mm**2 + y_mm**2) <= radius**2

def rasterize_cube(half_side, img_width_px, img_height_px, pixel_size):
    yy, xx = np.mgrid[0:img_height_px, 0:img_width_px]
    x_mm = (xx - img_width_px / 2) * pixel_size
    y_mm = (yy - img_height_px / 2) * pixel_size
    return (np.abs(x_mm) <= half_side) & (np.abs(y_mm) <= half_side)

def rasterize_polygons(polygons, center_x, center_y, img_width_px, img_height_px, pixel_size):
    img = Image.new('L', (img_width_px, img_height_px), 0)
    draw = ImageDraw.Draw(img)
    for poly in polygons:
        pixel_coords = []
        for x, y in poly.exterior.coords:
            px = (x - center_x) / pixel_size + img_width_px / 2
            py = (y - center_y) / pixel_size + img_height_px / 2
            pixel_coords.append((px, py))
        draw.polygon(pixel_coords, fill=255)

        for interior in poly.interiors:
            hole_coords = []
            for x, y in interior.coords:
                px = (x - center_x) / pixel_size + img_width_px / 2
                py = (y - center_y) / pixel_size + img_height_px / 2
                hole_coords.append((px, py))
            draw.polygon(hole_coords, fill=0)
    return np.array(img) > 0

def generate_layers(args):
    if args.shape == 'sphere':
        part_z_height = 2 * args.radius
        canvas_extent = 2 * args.radius
    elif args.shape == 'cube':
        part_z_height = args.side
        canvas_extent = args.side
    elif args.shape == 'cone':
        part_z_height = args.height
        canvas_extent = 2 * args.radius
    elif args.shape == 'from-mesh':
        mesh = trimesh.load(args.input)
        z_min, z_max = mesh.bounds[0][2], mesh.bounds[1][2]
        part_z_height = z_max - z_min
        x_extent = mesh.bounds[1][0] - mesh.bounds[0][0]
        y_extent = mesh.bounds[1][1] - mesh.bounds[0][1]
        canvas_extent = max(x_extent, y_extent)
        center_x = (mesh.bounds[1][0] + mesh.bounds[0][0]) / 2
        center_y = (mesh.bounds[1][1] + mesh.bounds[0][1]) / 2

    img_width_px = math.ceil(canvas_extent / args.pixel_size)
    img_height_px = img_width_px
    num_layers = math.ceil(part_z_height / args.layer_height)

    layers = []

    for i in range (num_layers):
        print(f"Generating layer {i+1}/{num_layers}", end='\r')
        z = i * args.layer_height + (args.layer_height / 2)

        if args.shape == 'sphere':
            cross_section_size = sphere_radius_at(z, args.radius)
        elif args.shape == 'cube':
            cross_section_size = cube_halfwidth_at(z, args.side)
        elif args.shape == 'cone':
            cross_section_size = cone_radius_at(z, args.radius, args.height)
        elif args.shape == 'from-mesh':
            cross_section_size = mesh_cross_section_at(mesh, z_min + z)

        if cross_section_size is None:
            mask = np.zeros((img_height_px, img_width_px), dtype=bool)
        elif args.shape == 'cube':
            mask = rasterize_cube(cross_section_size, img_width_px, img_height_px, args.pixel_size)
        elif args.shape == 'from-mesh':
            mask = rasterize_polygons(cross_section_size, center_x, center_y, img_width_px, img_height_px, args.pixel_size)
        else:
            mask = rasterize_circle(cross_section_size, img_width_px, img_height_px, args.pixel_size)

        layers.append(mask)

    return layers, img_width_px, img_height_px, num_layers

def save_layers(layers, out_folder):
    if os.path.exists(out_folder):
        shutil.rmtree(out_folder)
    os.makedirs(out_folder)
    for i, mask in enumerate(layers):
        img = Image.fromarray((mask * 255).astype(np.uint8))
        img.save(os.path.join(out_folder, f'layer_{i:04d}.png'))


def save_metadata(args, img_width_px, img_height_px, num_layers, out_folder):
    meta = {
        'pixel_size': args.pixel_size,
        'layer_height': args.layer_height,
        'img_width_px': img_width_px,
        'img_height_px': img_height_px,
        'num_layers': num_layers
    }

    with open(os.path.join(out_folder, 'meta.json'), 'w') as f:
        json.dump(meta, f, indent=4)

def main():
    args = parse_args()
    layers, img_width_px, img_height_px, num_layers = generate_layers(args)
    out_folder = os.path.join(args.out_folder, args.shape)
    save_layers(layers, out_folder)
    save_metadata(args, img_width_px, img_height_px, num_layers, out_folder)

if __name__ == '__main__':
    main()