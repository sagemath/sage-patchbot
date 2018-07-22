2.8.0 (unreleased)
==================

* First release covered by this changelog.
* Fixed bug with premature cleanup of the temporary SAGE_ROOT created for
  testing "unsafe" tickets.
* Fixed bug with resetting the current directory to the original build
  directory after a failed build of an "unsafe" ticket.

2.8.1
=====

* New plugins for pyflakes (all checks) and pycodestyle (W605 only).
* Reports now tell if sage is running on python2 or python3.
* Enhanced plugins to check for python3-incompatible code in .pyx
  and .py files.
