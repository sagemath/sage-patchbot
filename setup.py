import codecs
import os
import re

from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))

with open("README.md", "r") as fh:
    long_description = fh.read()


def read(*parts):
    return codecs.open(os.path.join(here, *parts), 'r').read()


def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


setup(name='sage-patchbot',
      version=find_version('sage_patchbot', 'version.py'),
      description='bot for automatic test of sagemath trac tickets',
      url='https://github.com/sagemath/sage-patchbot',
      author='Robert Bradshaw',
      license='GPL',
      long_description=long_description,
      long_description_content_type="text/markdown",
      url='https://github.com/sagemath/sage-patchbot'
      entry_points={
          'console_scripts': ['patchbot=sage_patchbot.patchbot:main']},
      packages=['sage_patchbot', 'sage_patchbot.server'],
      package_data={
          'sage_patchbot': [
              'serve.wsgi'
          ],
          'sage_patchbot.server': [
              'static/*.css',
              'images/*.png', 'images/*.svg',
              'templates/*.html', 'templates/*.svg', 'templates/*.txt'
          ]
      },
      classifiers=[
          "Programming Language :: Python :: 3"
          "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
          "Operating System :: OS Independent",
          "Topic :: Scientific/Engineering :: Mathematics",
          "Topic :: Software Development :: Quality Assurance"
      ]
      zip_safe=False)
