#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''The setup script.'''

from setuptools import setup


def readme():
    with open('README.md', 'r') as f:
        return f.read()


requirements = ['pyflakes', 'pycodestyle']


setup(name='sage_patchbot',
      version='2.8.2.dev0',
      description='bot for automatic test of sagemath trac tickets',
      long_description=readme(),
      long_description_content_type='text/markdown',
      keywords='sagemath',
      author='SageMath Developers',
      url='https://github.com/sagemath/sage-patchbot',
      license='GPL',
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
          'Programming Language :: Python :: 3',
          'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)',
          'Operating System :: OS Independent',
          'Topic :: Scientific/Engineering :: Mathematics',
          'Topic :: Software Development :: Quality Assurance'
      ],
      install_requires=requirements,
      zip_safe=False)
