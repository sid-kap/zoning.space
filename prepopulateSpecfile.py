"""
Generate a skeletal zone definition file from the given shapefile.
"""

import geopandas as gp
import re
import csv
from tempfile import mkdtemp
from shutil import rmtree
from os.path import basename, join, dirname
from time import strftime
from collections import defaultdict
from zipfile import ZipFile
from argparse import ArgumentParser
import numpy as np
import shapely.ops

from src.zoning.zoneingest import variables
from src.zoning.hooks import runHook

parser = ArgumentParser(description='Prepolate lookup table')
parser.add_argument('slug', metavar='slug', help='Slug for this dataset')
parser.add_argument('--out', metavar='out', help='Outfile (overrides default)')
parser.add_argument('--drop-small-zones', metavar='MIN_ZONE_SIZE', type=float, help='Drop zones smaller than MIN_ZONE_SIZE square km')
parser.add_argument('--imperial', action='store_true', help='Write specfile column names in imperial units')
args = parser.parse_args()

print('Reading data')
# TODO move this code into a module
tmp = mkdtemp()

datapath = join(dirname(__file__), 'data', 'zoning')
shpzip = join(datapath, args.slug + '.zip')
print(f'    Extracting shapefile {shpzip}...')
with open(shpzip, 'rb') as raw:
    zf = ZipFile(raw)

    shapePath = None

    for zi in zf.infolist():
        pth = zf.extract(zi, path=tmp) # Extract the item. zf.extract handles sanitizing member names
        if pth.endswith('.shp'):
            if shapePath is not None:
                raise ArgumentError(f'Multiple shapefiles found in {shpzip}!')
            else:
                shapePath = pth

print('    Reading shapefile...')
shp = gp.read_file(shapePath)
rmtree(tmp)

shp = runHook(args.slug, 'before', shp)

colsets = []
while True:
    print(f'Available columns: {", ".join(shp.columns.values)}')
    cols = input('Enter the columns to match on, separated by commas (done if finished)>')

    if cols == 'done':
        break

    cols = [c.strip() for c in cols.split(',')]

    if not all([col in shp.columns.values for col in cols]):
        print('some columns not found')
        continue
    else:
        colsets.append(cols)
        continue

cols = [col for cols in colsets for col in cols] # flatten array
cols = [col for i, col in enumerate(cols) if col not in cols[:i]] # uniquify preserving order

# convert all missing values to empty string
# because pandas is stupid...
for col in cols:
    shp[col] = shp[col].apply(lambda x: x if x is not None else '')

if args.drop_small_zones is not None:
    print(f'    Removing zones smaller than {args.drop_small_zones} square km...')
    minSizeSqM = args.drop_small_zones * (1000 ** 2) # convert to sq km
    # project to equal area projection for area calculation
    projected = shp[~shp.geometry.isnull()].to_crs('+proj=aea +lat_1=29.5 +lat_2=45.5 +lat_0=37.5 +lon_0=-96 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs')
    dissolve = projected.dissolve(cols)
    includeZones = dissolve[dissolve.area > minSizeSqM].index
    if len(cols) != 1:
        mask = shp[cols].apply(lambda x: tuple(x.values.tolist()) in includeZones, axis=1) # believe it or not this works
    else:
        mask = mask = shp[cols[0]].isin(includeZones.values)
    shp = shp[mask].copy()
    print(f'    Removed {len(dissolve) - len(includeZones)} small zones')

print(f'After filtering, {len(shp)} areas remain')

# Write the file
specfile = join(dirname(__file__), 'src', 'zoning', 'specs', args.slug + '.csv')
with open(specfile, 'w') as outf:
    writer = csv.writer(outf)

    # Write documentation
    writer.writerows([['// This is a spec file to parse zoning, generated by the autogeneration tool'],
                   ['// Any line with a first cell starting with // will be ignored, as will any cell starting with #, and any blank row'],
                   [f'// Generated: {strftime("%Y-%m-%d %H:%M:%S %Z")}']])

    # Write header
    writer.writerows([['jurisdiction', '', '# Jurisdiction represented by this zoning'],
                   ['data', '', '# URL for the data (not a direct download link, but the page describing the data)'],
                   ['year', '', '# Year the data was generated'],
                   ['code', '', '# URL of the zoning code used to fill out this file']])

    writer.writerows([['column', col, '# Columns specifying unique zones'] for col in cols])

    writer.writerows([[],
                   ['// Each table below contains one component of the zone designator'],
                   ['// They will be applied in order, so information from later tables will override information from previous tables'],
                   ['// These are the canonical forms of variables. Anything ending in meters can also be expressed in feet, hectares can'],
                   ['// also be expressed in acres or square feet, and minLotSizePerUnit{Hectares|Acres|SqFt} will be'],
                   ['// converted to maxUnitsPerHectare, by simply changing the variable names']
    ])

    print('Finding unique zone codes')
    for colset in colsets:
        zoneCodes = defaultdict(set)

        relevantCols = shp[colset].drop_duplicates().sort_values(colset)

        attrs = [v for v in variables.keys() if v != 'zone']

        if args.imperial:
            attrs = [a.replace('Hectares', 'Acres').replace('Meters', 'Feet') for a in attrs]

        writer.writerow(colset + attrs)
        relevantCols.apply(lambda x: writer.writerow(x.values.tolist()), axis=1)
        writer.writerow([])
