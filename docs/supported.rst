==================
Supported Software
==================

Client applications
===================

The following software has been reported to work well with vdirsyncer, however,
none of it is regularly tested.

Calendars
---------

- `khal <http://lostpackets.de/khal/>`_, a CLI calendar application supporting
  :doc:`vdir <vdir>`. You can use
  :py:class:`vdirsyncer.storage.FilesystemStorage` with it.

- The graphical calendar app `Orage <http://www.kolumbus.fi/~w408237/orage/>`_
  saves a calendar in a single ``.ics`` file. You can use
  :py:class:`vdirsyncer.storage.SingleFileStorage` with it.

- The graphical calendar app `dayplanner <http://www.day-planner.org/>`_ seems
  to store its data in a single ``.ics`` file. Therefore you should be able to
  use :py:class:`vdirsyncer.storage.SingleFileStorage` with it.

Contacts
--------

- `khard <http://github.com/scheibler/khard/>`_, a commandline addressbook
  supporting :doc:`vdir <vdir>`. You can use
  :py:class:`vdirsyncer.storage.FilesystemStorage` with it.

- `The ppl addressbook <http://ppladdressbook.org/>`_ uses a storage format
  similar to :doc:`vdir <vdir>`. There are some pitfalls though, `see the notes
  on the related issue <https://github.com/hnrysmth/ppl/issues/47>`_.

- `contactquery.c <https://github.com/t-8ch/snippets/blob/master/contactquery.c>`_,
  a small program explicitly written for querying vdirs from mutt.

Supported servers
=================

vdirsyncer is currently regularly and automatically tested against the latest
versions of Radicale and ownCloud. In principle, vdirsyncer is supposed to run
correctly with any remotely popular CalDAV or CardDAV server.

vdirsyncer's synchronization works best if the items have ``UID`` properties.
Items which don't have this property still should be synchronized fine as of
version 1.5, but for performance reasons, such items should rather be the
exception than the rule. For a possible way to automatically fix such items,
take a look at `vfix <https://github.com/geier/vfix>`_.

Radicale
--------

Vdirsyncer is tested against the git version and the latest PyPI release of
Radicale.

- Radicale doesn't `support time ranges in the calendar-query of CalDAV
  <https://github.com/Kozea/Radicale/issues/146>`_, so setting ``start_date``
  and ``end_date`` for :py:class:`vdirsyncer.storage.CaldavStorage` will have
  no or unpredicted consequences.

- `Versions of Radicale older than 0.9b1 choke on RFC-conform queries for all
  items of a collection <https://github.com/Kozea/Radicale/issues/143>`_.

  Vdirsyncer's default value ``'VTODO, VEVENT'`` for
  :py:class:`vdirsyncer.storage.CaldavStorage`'s ``item_types`` parameter will
  work fine with these versions, and so will all values, except for the empty
  one.

  The empty value ``''`` will get vdirsyncer to send a single HTTP request to
  fetch all items, instead of one HTTP request for each possible item type. As
  the linked issue describes, old versions of Radicale expect a
  non-RFC-compliant format for such queries, one which vdirsyncer doesn't
  support.

ownCloud
--------

Vdirsyncer is tested against the latest version of ownCloud.

- *Versions older than 7.0.0:* ownCloud uses SabreDAV, which had problems
  detecting collisions and race-conditions. The problems were reported and are
  fixed in SabreDAV's repo, and the corresponding fix is also in ownCloud since
  7.0.0. See `Bug #16 <https://github.com/untitaker/vdirsyncer/issues/16>`_ for
  more information.