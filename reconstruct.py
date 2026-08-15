import json
import os
from PIL import Image
import numpy as np
from skimage.measure import marching_cubes
import trimesh
import argparse


def load_metadata(folder):
    path = os.path.join(folder, 'meta.json')
    with open(path) as f:
        meta = json.load(f)
    return meta

def load_layers(folder):
    filenames = sorted(f for f in os.listdir(folder) if f.endswith('.png'))

    layers = []

    for i, filename in enumerate(filenames):
        print(f"Loading layer {i+1}/{len(filenames)}", end='\r')
        path = os.path.join(folder, filename)
        img = Image.open(path)
        img_array = np.array(img)
        mask = img_array > 128
        layers.append(mask)

    print()
    return layers

def build_volume(layers):
    volume = np.stack(layers, axis=0)
    volume = np.pad(volume, pad_width=1, mode='constant', constant_values=False)
    return volume

def extract_mesh(volume):
    verts, faces, _, _ = marching_cubes(volume, level=0.5)
    return verts, faces

def scale_vertices(verts, pixel_size, layer_height):
    x = verts[:, 2] * pixel_size
    y = verts[:, 1] * pixel_size
    z = verts[:, 0] * layer_height
    return np.stack([x, y, z], axis=1)

def compare_extents(mesh, original_path):
    original = trimesh.load(original_path)
    print('original extents (mm):     ', original.extents)
    print('reconstructed extents (mm):', mesh.extents)

def export_mesh(verts, faces, output_path, view=False, compare_to=None, color=None):
    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.export(output_path)
    if compare_to:
        compare_extents(mesh, compare_to)
    if view:
        if color:
            mesh.visual.face_colors = color + [255]
        mesh.show()

def parse_args():
    parser = argparse.ArgumentParser(description='Reconstruct a 3D mesh from PNG layer slices')
    parser.add_argument('folder', help='folder containing PNG slices and meta.json')
    parser.add_argument('-o', '--output', required=True, help='output STL path')
    parser.add_argument('--view', action='store_true', help='open interactive viewer after export')
    parser.add_argument('--compare-to', help='original mesh file to compare reconstructed extents against')
    parser.add_argument('--color', nargs=3, type=int, metavar=('R', 'G', 'B'), help='viewer color, e.g. --color 100 150 200')
    return parser.parse_args()


def main():
    args = parse_args()
    meta = load_metadata(args.folder)
    layers = load_layers(args.folder)
    volume = build_volume(layers)
    verts, faces = extract_mesh(volume)
    verts = scale_vertices(verts, meta['pixel_size'], meta['layer_height'])
    export_mesh(verts, faces, args.output, view=args.view, compare_to=args.compare_to, color=args.color)

if __name__ == '__main__':
    main()

