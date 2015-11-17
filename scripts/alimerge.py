#!/opt/rh/python33/root/usr/bin/python

import sys, _import_hack # _import_hack assumes that the numtheory package is in the parent directory of this directory
                         # this should be removed when proper pip installation is supported (and ad hoc python scripts are no longer necessary)

from time import strftime
import json
from myutils import email

JSON = '/var/www/rechenkraft.net/aliquot/AllSeq.json'
def Print(*args):
     print(strftime('%F - %H:%M:%S'), *args)
Print('Merge finder starting')

with open(JSON, 'r') as f: # Read current table data
          olddat = json.load(f)['aaData']

ids = {}
for ali in olddat:
     this = ali[3] # this = ali.id
     current = ids.get(this) # I'm assuming/hoping this is O(1), i.e. independent of the size of the dict
     if current is None: # No match for this id
          ids[this] = ali[0] # ids[this] = ali.seq # this id corresponds to this seq
     else: # found a match (i.e. a merge)
          seq = ali[0]
          if seq > current:
               big = seq, current
          else:
               big = current, seq
          Print(big[0], 'seems to have merged with', big[1])
          try:
               email('Aliquot merge!', '{} seems to have merged with {}'.format(big[0], big[1]))
          except Exception as e:
               Print("alimerge email failed")

Print('Merge finder finished')
