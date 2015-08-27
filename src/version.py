import os
import subprocess


def get_version():
    """
    Get the version of sage-patchbot.

    First try using git tags then using the version.txt file.

    The expected result is a string of the shape '2.2' or '2.3.rc0'.

    EXAMPLES::

        sage: from version import get_version
        sage: get_version()
        '2.3.2'
    """
    src_dir = os.path.dirname(os.path.abspath(__file__))
    top = os.path.dirname(src_dir)
    git_dir = os.path.join(top, '.git')
    if os.path.exists(git_dir):
        try:
            ver = subprocess.check_output(['git', '--work-tree=' + top,
                                           '--git-dir=' + git_dir,
                                           'describe', '--tags', '--dirty'])
            return ver.strip().decode('utf8')
        except:
            pass
    version_file = os.path.join(src_dir, 'version.txt')
    if os.path.exists(version_file):
        return open(version_file).read().strip().decode('utf8')
    # failure
    return "unknown"

if __name__ == '__main__':
    print(get_version())
