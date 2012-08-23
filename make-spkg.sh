#/bin/bash

VERSION=1.0
REV=HEAD

rm -rf workspace-*
TMP=$(mktemp -d workspace-XXXXXX)
ORIGINAL=$(pwd)

if [ ! -e upstream ]
then
    git clone git://github.com/robertwb/sage-patchbot.git upstream
fi

cd $TMP
sage -hg clone $ORIGINAL patchbot-$VERSION

git clone $ORIGINAL/upstream patchbot-$VERSION/src
cd patchbot-$VERSION/src
git checkout -q $REV
rm -rf .git
cd ../..

sage -spkg patchbot-$VERSION
cp patchbot-$VERSION.spkg $ORIGINAL

cd $ORIGINAL
rm -rf $TMP

hg status
