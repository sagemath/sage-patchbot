"""
Update the version of sage_patchbot from git.

The version is found using git tags, and then stored in the version.py
file under the format

__version__ = '2.2.2'
"""
import os
import subprocess

src_dir = os.path.dirname(os.path.abspath(__file__))
top = os.path.dirname(src_dir)
git_dir = os.path.join(top, '.git')
assert os.path.exists(git_dir)
ver = subprocess.check_output(['git', '--work-tree=' + top,
                               '--git-dir=' + git_dir,
                               'describe', '--tags', '--dirty'])
ver_str = ver.strip().decode('utf8')

version_file = os.path.join(src_dir, 'version.py')
with open(version_file, 'wt') as f:
    f.write("__version__ = '{}'\n".format(ver_str))
