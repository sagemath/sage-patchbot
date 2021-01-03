3.0.2
=====

* Handle gracefully lazy imports on several consecutive lines
* In the webpages for plugin reports, links to wiki explanations.
* Remove plugins about python3 check, add plugin about deprecation number
* Larger set of files are declared "safe"
* Ability to handle more machine names

3.0.1 (unreleased)
==================

* Nothing changed yet.


3.0.0 (2019-12-13)
==================

* Remove calls to deprecated method of platform.
* More plugin checks: Returns, "space:"
* Now Python 3 only.
* Fixed printing of return values from report posts to not print
  bytes objects.


2.8.1 (2018-08-08)
==================

* Fixed a regression from 2.8.0 that could occur when testing just
  ticket 0 (#132).


2.8.0 (2018-07-23)
==================

* First release covered by this changelog.
* New plugins for pyflakes (all checks) and pycodestyle (W605 only).
* Reports now tell if sage is running on python2 or python3.
* Enhanced plugins to check for python3-incompatible code in .pyx
  and .py files.
* Fixed bug with premature cleanup of the temporary SAGE_ROOT created for
  testing "unsafe" tickets.
* Fixed bug with resetting the current directory to the original build
  directory after a failed build of an "unsafe" ticket.
