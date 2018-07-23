2.8.1 (unreleased)
==================

* Nothing changed yet.


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
