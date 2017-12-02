# This is written to Python 3.3 standards
# indentation: 5 spaces (personal preference)
# when making large scope switches (e.g. between def or class blocks) use two
# blank lines for clearer visual separation

#    Copyright (C) 2014-2015 Bill Winslow
#
#    This module is a part of the mfaliquot package.
#
#    This program is libre software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
#    See the LICENSE file for more details.


################################################################################
# This module contains the primary reservation spidering and update logic
# It delegates heavily to forum_xaction for such

from .forum_xaction import spider_res_thread, SEQ_REGEX
from . import DATETIMEFMT
from ..myutils import blogotubes
from time import strftime, gmtime
import logging

_logger = logging.getLogger(__name__)


class ReservationsSpider: # name is of debatable good-ness
     '''A class to manage the statefulness of spidering the MersenneForum res
     thread. Delegates the primary spidering logic to the module level functions.'''

     def __init__(self, seqinfo, pidfile):
          '''`seqinfo` should be a SequencesManager instance. It is assumed to
          already have acquired its lock.'''
          self.seqinfo = seqinfo
          self.pidfile = pidfile


     def spider_all_apply_all(self, mass_reses):
          try:
               with open(self.pidfile, 'r') as f:
                    last_pid = int(f.read())
          except FileNotFoundError:
               last_pid = None

          last_pid, *other = update_apply_all_res(self.seqinfo, last_pid, mass_reses)

          with open(self.pidfile, 'w') as f:
               f.write(str(last_pid) + '\n')

          return other[1:] # other[0] == prev_pages


################################################################################


# First the standalone func that processes mass text file reservations
def parse_mass_reservation(reservee, url):
     '''Parses a '\n' separated list of sequences, to be reserved to the given
     name. Returns (current_entries, duplicate_seqs, unknown_lines)'''
     #global email_msg
     txt = blogotubes(url)
     current, dups, unknowns = set(), [], []
     for line in txt.splitlines():
          if SEQ_REGEX.match(line):
               seq = int(line)
               if seq in current:
                    _logger.warning("mass reservation: mass res-er {} listed a duplicate for {}".format(name, seq))
                    dups.append(seq)
               else:
                    current.add(seq)
          elif not re.match(r'^[0-9]+$', line): # don't remember what purpose this line serves, ignoring any number-shaped thing that isn't a 5-7 digit sequence
               _logger.warning("mass reservation: unknown line from {}: '{}'".format(name, line))
               unknowns.append(line)
     return current, dups, unknowns


def update_apply_all_res(seqinfo, last_pid, mass_reses):
     '''Searches all known reservations, returning compiled reses to be applied,
     as well as various results from subordinate functions'''

     now = strftime(DATETIMEFMT, gmtime())

     last_pid, prev_pages, thread_res = spider_res_thread(last_pid)

     mass_adds = []
     mass_reses_out = []
     for reservee, url in mass_reses.items():
          current, dups, unknowns = parse_mass_reservation(reservee, url)
          old = set(ali.seq for ali in seqinfo.values() if ali.res == reservee)
          drops = old - current
          adds = current - old
          dropres = seqinfo.unreserve_seqs(reservee, drops)
          mass_adds.append((reservee, adds))
          mass_reses_out.append([reservee, dups, unknowns, dropres])

     out = []
     for name, adds, drops in thread_res:
          addres = seqinfo.reserve_seqs(name, adds)
          dropres = seqinfo.unreserve_seqs(name, drops)
          out.append((name, addres, dropres))

     for name_adds, lst in zip(mass_adds, mass_reses_out):
          lst.append(seqinfo.reserve_seqs(*name_adds))

     seqinfo.resdatetime = now

     return last_pid, prev_pages, out, mass_reses_out # What a mess of data

