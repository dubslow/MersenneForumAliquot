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
#
# This is the central file of the application module, providing the common classes
# used by scripts implementing the procedural stuff.
#
# AliquotSequence is the standard record of information for one single sequence,
# and is the primary ingredient in the AllSeq.json/.html files.
#
# _SequencesData is the (private) class which is in charge of reading and writing
# to the Master Data File, AllSeq.json. It is not intended to be exported. It
# contains the (public) methods which manipulate its private data structures.
#
# SequencesManager subclasses _SequencesData, and implements the common algorithms
# which would be useful to scripts attempting to use the underlying data in the
# file. These algorithms needn't the private data, and for these two reasons is
# it a separate subclass, and also thus exportable. (The public methods of its
# parent are also exposed here.)
#
################################################################################


import json
from collections import defaultdict, Counter
import datetime
from time import sleep
from os import remove as rm

DATETIMEFMT = '%Y-%m-%d %H:%M:%S'


################################################################################
# First, the AliquotSequence class:

# A common class to multiple scripts. It uses a secret dictionary to map attributes
# to list form, which is handy for trivial JSONification. Perhaps not the best
# design, but the inexperienced me fresh to Python and OOP went power crazy with
# __getattribute__ and __setattr__, and I can certainly think of worse ways of
# doing this

class AliquotSequence(list):
     _map = {'seq':      (0, None), # (list_index, default_val)
             'size':     (1, None),
             'index':    (2, None),
             'guide':    (3, ''),
             'factors':  (4, ''),
             'cofactor': (5, ''),
             'klass':    (6, None),
             'res':      (7, ''),
             'progress': (8, None),
             'time':     (9, ''),
             'nzilch':   (10, 0), # Definitely looking for a better name
             'priority': (11, 0),
             'id':       (12, None),
             'driver':   (13, None)
            }
     _defaults = [None] * len(_map)
     for attr, tup in _map.items():
          _defaults[tup[0]] = tup[1]


     def __setattr__(self, name, value):
          try:
               # Attributes are secretly just a specific slot on the list
               self[AliquotSequence._map[name][0]] = value
          except KeyError:
               super().__setattr__(name, value)


     def __getattribute__(self, name):
          try:
               return self[AliquotSequence._map[name][0]]
          except KeyError:
               return super().__getattribute__(name)


     def __init__(self, **kwargs):
          '''This recognizes all valid attributes, as well as the 'lst' kwarg
          to convert from list format (must be correct length).'''
          # Not exactly the prettiest code, but it's very general code
          # First super().__init__ as appropriate
          if 'lst' in kwargs:
               l = kwargs['lst']
               a = len(l)
               b = len(self._map)
               if a != b:
                    raise ValueError('AliquotSequence.__init__ received invalid size list (got {}, must be {})'.format(a, b))
               super().__init__(l)
               del kwargs['lst']
          else:
               super().__init__(self._defaults)

          for kw, val in kwargs.items():
               if kw not in self._map:
                    raise TypeError("unknown keyword arugment {}".format(kw))
               self.__setattr__(kw, val)


     def is_valid(self):
          return self.seq and (self.size and self.size > 0) and (self.index and self.index > 0) and self.factors


     def __str__(self):
          if self.is_valid():
               return "{:>7d} {:>5d}. sz {:>3d} {:s}".format(self.seq, self.index, self.size, self.factors)
          else:
               raise ValueError('Not fully described! Seq: '+str(self.seq))


     def reservation_string(self):
          '''str(AliquotSequence) gives the AllSeq.txt format, this gives the MF reservations post format'''
          #    966  Paul Zimmermann   893  178
          # 933436  unconnected     12448  168
          if not self.res:
               return ''
          out = "{:>7d}  {:30s} {:>5d}  {:>3d}\n".format(self.seq, self.res, self.index, self.size)
          return out


     def calculate_priority(self, max_update_period=90, begin_time_penalty=30, res_factor=1/2):
          '''Arguments are as follows: `max_update_period` is the target maximum
          time between updates for any sequence no matter how infrequent it is,
          in days. `begin_time_penalty` is the age (in days) at which priority
          is directly affected by the previous value (before that, it's just
          how many stationary updates).'''
          # The biggest problem with this prio algo is that I had only planned that
          # `allseq.py` runs it for newly-updated seqs -- so the old seqs will
          # never get their prio actually updated towards 0, even if they should be
          # Solution: add a third script run once a day to update *all* prios?

          prio = self.nzilch

          if isinstance(self.progress, str):
               lastprog = datetime.date(*(int(x) for x in self.progress.split('-')))
               now = datetime.datetime.utcnow().date()
               deltadays = (now - lastprog).days

               if deltadays > begin_time_penalty:
                    # We apply a "penalty" to priority based on the seq's "progress"
                    # from btp to mup. So when deltadays == btp, penalty factor = 0,
                    # and when deltadays == mup, penalty factor = 1, with linear
                    # interpolation. Then prio -= prio * penalty factor.
                    # The idea is that prio is necessarily 0 at m_u_p.
                    days -= begin_time_penalty
                    max_update_period -= begin_time_penalty
                    ratio = (deltadays - begin_time_penalty)/(max_update_period - begin_time_penalty)

                    prio *= (1-ratio)

          if self.res:
               prio *= res_factor

          self.priority = round(prio, 3)

#
#
################################################################################

################################################################################
# Next would be _SequenceData, but first two helper pieces: custom_inherit, used
# by _SequencesData to delegate some methods from itself to its underlying
# dictionary, and a thin wrapper class around stdlib.heapq (can't believe that
# doesn't already exist)
#
# First, custom_inherit and its own helper _DelegatedAttribute

# Following class and decorator totally jacked from Jacob Zimmerman, with a few tweaks/renames
# https://programmingideaswithjake.wordpress.com/2015/05/23/python-decorator-for-simplifying-delegate-pattern/
#
# I couldn't get the default copying of the entire baseclass dict to work, because that delegates __new__,
# which chokes on the non-subclass subclass. So just remove it for now

class _DelegatedAttribute:
    def __init__(self, delegator_name, attr_name, baseclass):
        self.attr_name = attr_name
        self.delegator_name = delegator_name
        self.baseclass = baseclass

    def __get__(self, instance, klass):
        if instance is None:
            # klass.DelegatedAttr() -> baseclass.attr
            return getattr(self.baseclass, self.attr_name)
        else:
            # instance.DelegatedAttr() -> instance.delegate.attr
            return getattr(self.delegator(instance), self.attr_name)

    def __set__(self, instance, value):
        # instance.delegate.attr = value
        setattr(self.delegator(instance), self.attr_name, value)

    def __delete__(self, instance):
        delattr(self.delegator(instance), self.attr_name)

    def delegator(self, instance):
        # minor syntactic sugar to help remove "getattr" spam (marginal utility)
        return getattr(instance, self.delegator_name)

    def __str__(self):
        return ""


def _custom_inherit(baseclass, delegator='delegate', include=None, exclude=None):
    '''A decorator to customize inheritance of the decorated class from the
    given baseclass. `delegator` is the name of the attribute on the subclass
    through which delegation is done;  `include` and `exclude` are a whitelist
    and blacklist of attrs to include from baseclass.__dict__, providing the
    main customization hooks.'''
    # `autoincl` is a boolean describing whether or not to include all of baseclass.__dict__

    # turn include and exclude into sets, if they aren't already
    if not isinstance(include, set):
        include = set(include) if include else set()
    if not isinstance(exclude, set):
        exclude = set(exclude) if exclude else set()

    # delegated_attrs = set(baseclass.__dict__.keys()) if autoincl else set()
    # Couldn't get the above line to work, because delegating __new__ fails miserably
    delegated_attrs = set()
    attributes = include | delegated_attrs - exclude

    def wrapper(subclass):
        ## create property for storing the delegate
        #setattr(subclass, delegator, None)
        # ^ Initializing the delegator is the duty of the subclass itself, this
        # decorator is only a tool to create attrs that go through it

        # don't bother adding attributes that the class already has
        attrs = attributes - set(subclass.__dict__.keys())
        # set all the attributes
        for attr in attrs:
            setattr(subclass, attr, _DelegatedAttribute(delegator, attr, baseclass))
        return subclass

    return wrapper

#
################################################################################
# Next, the other helper for _SequencesData: class wrapper around stdlib.heapq

import heapq as _heap
from functools import partial

class _Heap(list):

     def __init__(self, iterable=()):
          if iterable:
               super().__init__(iterable)
          else:
               super().__init__()

          self.push = partial(_heap.heappush, self)
          self.pop = partial(_heap.heappop, self)
          self.pushpop = partial(_heap.heappushpop, self)
          self.replace = partial(_heap.heapreplace, self)
          self.heapify = partial(_heap.heapify, self)

          self.heapify()

#
################################################################################
# Next, _SequencesData, the private class implementing the underlying dictionary
# and priority heap. For subclasses/external use, the dictionary is only directly
# accessible in an immutable fashion (though the public methods mutate it as
# necessary)

# Error raised if the file can't be locked
class LockError(Exception): pass


@_custom_inherit(dict, delegator='_data', include=['__len__', '__getitem__',
                   '__contains__', 'get', 'items', 'keys', 'values', '__str__'])
class _SequencesData:
     '''The class that reads and writes The Sequence Data File. The `file`
     constructor argument is immutable for the lifetime of the object. Writing
     also writes to the other two files (which are read-only).'''

     def __init__(self, jsonfile, txtfile=''):
          '''Create the object with its one and only jsonfile. To switch files,
          you must finalize this object and "manually" move the file, then make
          a new SequenceData object.'''
          # For priority purposes, we keep the jsonlist in minheap form ordered
          # by priority. The dict is an access convenience for most purposes.
          if jsonfile == txtfile:
               raise ValueError("file arguments must be unique!")
          self._jsonfile = jsonfile
          self._lockfile = self._jsonfile + '.lock'
          if not txtfile:
               txtfile = jsonfile.replace('.json', '.txt')
          self._txtfile = txtfile
          self._data = None # Will cause errors if you try and use this class
          self._heap = None # before actually reading data

     # See heap_impl_details.txt for a detailed rationale for the heap design.
     # The gist is we just use standard heap methods for everything; dropping
     # seqs nukes the relevant heap entry, so heap-read methods must error check
     # for valid entries

     @property
     def file(self):
          return self._jsonfile


     def _lock(self):
          try:
               open(self._lockfile, 'x').close()
          except FileExistsError:
               raise LockError("Lock file {} exists, _SequencesData uninitialized".format(self._lockfile)) from None


     def _unlock(self):
          rm(self._lockfile) # Should we test for problems or just let exceptions propgate?


     def _lock_init_empty(self):
          '''Use if starting from scratch, not reading from file'''
          self._lock()
          self._data = dict()
          self._heap = _Heap()


     def lock_read_init(self):
          '''Initialize self from the (immutable attribute) `file` passed to the constructor.'''
          self._lock()

          with open(self.file, 'r') as f:
               data = json.load(f)

          heap = data['aaData']
          if 'resdatetime' in data:
               self.resdatetime = data['resdatetime']

          self._data = dict()
          self._heap = _Heap([None])
          self._heap *= len(heap)
          # Heap/list constructors copy their input, so multiply after constructor

          for i, dat in enumerate(heap):
               ali = AliquotSequence(lst=dat)
               self._heap[i] = self._make_heap_entry(ali)
               self._data[ali.seq] = ali

          self._heap.heapify()


     def acquire_lock(self, *, block_minutes=0):
          '''Use this to begin a `with` statement'''
          # Dummy function for code clarity and "argument passing"
          self._block_minutes = block_minutes
          return self


     def __enter__(self):
          # Thin wrapper around lock_read_init, only difference is artifical blocking
          seconds = self._block_minutes*60
          period = 1 # No idea if this is sane or not
          count = seconds // period
          for i in range(count + 1): # Ensure at least one loop per iter
               # only problem: even "non blocking" one iter sleeps for one period
               try:
                    self.lock_read_init()
               except LockError as e:
                    f = e
               else:
                    return self
               sleep(period)
          raise f


     def unlock_write(self):
          '''Finalize self to the given file(s). Totally overwrites it with data
          from self.'''
          # ignore dropped seqs (HEAPENTRY)
          out = [item[2] for item in self._heap if item[2] in self._data]
          # Find seqs that have been dropped from heap, they're just appended
          # at the end, no heapifying
          missing = set(self._data.keys()).difference(out)
          out = [self._data[seq] for seq in out]
          out.extend(self._data[seq] for seq in missing)

          outdict = {"aaData": out}
          try:
               outdict['resdatetime'] = self.resdatetime
          except Exception:
               pass
          json_string = json.dumps(outdict).replace('],', '],\n') + '\n'
          with open(self._jsonfile, 'w') as f:
               f.write(json_string)
          del json_string

          if self._txtfile:
               txt_string = '\n'.join(str(ali) for ali in sorted(out, key=lambda ali: ali.seq)) + '\n'
               with open(self._txtfile, 'w') as f:
                    f.write(txt_string)
               del txt_string

          del out

          self._unlock()


     def __exit__(self, *exc):
          self.unlock_write()


     @staticmethod
     def _make_heap_entry(ali):
          # Must be sabotage-able, i.e. mutable, can't use tuple
          # All code that depends on the format here is tagged with HEAPENTRY
          entry = [ali.priority, ali.time, ali.seq]
          ali._heap_entry = entry # We need to track heap entries so we can sabotage them upon seqdrop
          return entry


     @staticmethod
     def _sabotage_heap_entry(ali):
          # HEAPENTRY
          ali._heap_entry[2] = None


     def pop_n_todo(self, n):
          '''A lazy iterator yielding the n highest priority sequences'''
          while n > 0:
               seq = self._heap.pop()[2] # HEAPENTRY
               if seq: # heap entries are sabotaged by setting seq==0
                    n -= 1
                    yield seq


     def drop(self, seqs):
          '''Drop the given sequences from the dictionary. Raises an exception
          if a seq doesn't exist.'''
          for seq in seqs:
               if seq not in self:
                    raise KeyError("seq {} not in seqdata".format(seq))
               ali = self._data[seq]
               self._sabotage_heap_entry(ali)
               del self._data[seq]
               del ali


     def push_new_info(self, ali):
          '''Call this method to insert a newly updated AliquotSequence object
          into the underlying datastructures. Any previous such object is
          silently overwritten.'''
          self._data[ali.seq] = ali
          self._heap.push(self._make_heap_entry(ali))

#
#
################################################################################

################################################################################
# Finally, we get to the public class. It exposes the public methods of its
# parent (which are those that require direct access to the underlying
# datastructures) in addition to defining the common algorithms useful for scripts
# operating on the data which *don't* require special access. Since there are
# several such algorithms, they are separated out into this public class.

class SequencesManager(_SequencesData):
     '''The public class which implements the basic methods to manipulate
     aliquot sequence data, as well as several common algorithms on top of the
     basic methods. Update the resdatetime attribute when reservations are
     spidered.'''

     def find_merges(self):
          '''Returns a tuple of (mergee, (*mergers)) tuples (does not drop)'''
          ids = defaultdict(list)
          for ali in self.values():
               ids[ali.id].append(ali.seq)

          merges = [list(sorted(lst)) for lst in ids.values() if len(lst) > 1]

          return tuple((lst[0], lst[1:]) for lst in merges)


     def reserve_seqs(self, name, seqs):
          '''Mark the `seqs` as reserved by `name`. Raises ValueError if a seq
          doesn't exist. Returns (DNEs, already_owns, other_owns)'''
          DNEs, already_owns, other_owns = [], [], []
          for seq in seqs:
               if seq not in self:
                    DNEs.append(seq)
                    continue

               other = self[seq].res

               if not other:
                    self[seq].res = name
               elif name == other:
                    already_owns.append(seq)
               else:
                    other_owns.append((seq, other))

          return DNEs, already_owns, other_owns


     def unreserve_seqs(self, name, seqs):
          '''Mark the `seqs` as no longer reserved. Raises ValueError if seq does
          not exist. Returns (DNEs, not_reserveds, wrong_reserveds, count_dropped) '''
          DNEs, not_reserveds, wrong_reserveds = [], [], []
          for seq in seqs:
               if seq not in self:
                   DNEs.append(seq)
                   continue

               current = self[seq].res

               if not current:
                    not_reserveds.append(seq)
               elif name == current:
                    self[seq].res = ''
               else:
                    wrong_reserveds.append((seq, current))

          return DNEs, not_reserveds, wrong_reserveds


     def calc_common_stats(self):

          sizes = Counter(); lens = Counter(); guides = Counter(); progs = Counter(); cofacts = Counter()
          totsiz = 0; totlen = 0; avginc = 0; totprog = 0
          for ali in self.values():
               sizes[ali.size] += 1; totsiz += ali.size
               lens[ali.index] += 1; totlen += ali.index
               guides[ali.guide] += 1; avginc += ali.index/ali.size
               progs[ali.progress] += 1
               cofacts[ali.cofactor] += 1

               if isinstance(ali.progress, int):
                    totprog += 1

          # Put stats table in json-able format
          data_total = len(self)
          lentable = []; lencount = 0
          sizetable = [ [key, value] for key, value in sizes.items() ]
          cofactable = [ [key, value] for key, value in cofacts.items() ]
          for leng, cnt in sorted(lens.items(), key=lambda tup: tup[0]):
               lentable.append( [leng, cnt, "{:2.2f}".format(lencount/(data_total-cnt)*100)] )
               lencount += cnt
          guidetable = [ [key, value] for key, value in guides.items() ]
          progtable = [ [key, value] for key, value in progs.items() ]

          # see allseq.py for use
          return sizetable, cofactable, guidetable, progtable, lentable, totlen/totsiz, avginc/data_total, totprog, totprog/data_total



