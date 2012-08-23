#/bin/bash

VERSION=HEAD

rm -rf workspace-*
TMP=$(mktemp -d workspace-XXXXXX)
ORIGINAL=$(pwd)

if [ ! -e .git ]
then
    echo "make-spkg only works from within a git repo"
    echo "please run"
    echo "    git clone git://github.com/robertwb/sage-patchbot.git upstream"
    exit 1
fi

status=$(sage -hg status)
if [ -n "$status" ]; then
    echo "Uncommitted changes."
    echo "$status"
    exit 1
fi

cd $TMP
sage -hg clone $ORIGINAL patchbot-$VERSION

git clone $ORIGINAL/upstream patchbot-$VERSION/src
cd patchbot-$VERSION/src
git checkout -q $VERSION
rm -rf .git
cd ../..

sage -spkg patchbot-$VERSION
cp patchbot-$VERSION.spkg $ORIGINAL

cd $ORIGINAL
rm -rf $TMP
