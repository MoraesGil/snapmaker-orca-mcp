"""Bounded independent geometry and native layer observations."""
import zipfile
from xml.etree import ElementTree as ET
from .http_transport import gcode

def bounds(path):
    with zipfile.ZipFile(path) as archive:
        model = ET.fromstring(archive.read('3D/3dmodel.model'))
    objects = {int(node.get('id')): node for node in model.findall('./{*}resources/{*}object')}
    def apply(point, transform):
        x, y, z = point
        return [transform[i] * x + transform[3+i] * y + transform[6+i] * z + transform[9+i] for i in range(3)]
    def matrix(value):
        return [float(x) for x in (value or '1 0 0 0 1 0 0 0 1 0 0 0').split()]
    def vertices(oid, trail=()):
        assert oid not in trail
        obj = objects[oid]
        result = [[float(vertex.get(k)) for k in ('x', 'y', 'z')]
                  for vertex in obj.findall('./{*}mesh/{*}vertices/{*}vertex')]
        for component in obj.findall('./{*}components/{*}component'):
            assert not any(key.endswith('path') for key in component.attrib)
            transform = matrix(component.get('transform'))
            result.extend(apply(p, transform) for p in vertices(int(component.get('objectid')), (*trail, oid)))
        return result
    result = {}
    for item in model.findall('./{*}build/{*}item'):
        oid = int(item.get('objectid'))
        transform = matrix(item.get('transform'))
        points = [apply(point, transform) for point in vertices(oid)]
        assert points
        result[oid] = [[min(p[i] for p in points), max(p[i] for p in points)] for i in range(3)]
    return result

def z_levels(job):
    return sorted({move['z'] for move in gcode(job)['moves'] if move['feature'] == 'Outer wall'})
