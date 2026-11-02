#!/bin/sh
# Morpheus (Perseus's morphological analyser, the perseids-tools build) into data/morpheus,
# for the library's search (scripts/morpheus.py). Needs git, gcc, make and flex; the
# stemlibs come built. GCC >= 15 defaults to C23, so the 1980s C needs -std=gnu89 and a
# few warnings relaxed; flex's runtime library (libfl) is often missing, and only supplies
# yywrap() and a trivial main(), so a local stub stands in.
set -e
cd "$(dirname "$0")/../data"
[ -d morpheus ] || git clone --depth 1 https://github.com/perseids-tools/morpheus.git morpheus
cd morpheus
mkdir -p stubs
printf 'int yywrap(void) { return 1; }\n' > stubs/yywrap.c
printf 'int yylex(void);\nint main(void) { while (yylex() != 0) ; return 0; }\n' > stubs/libmain.c
(cd stubs && gcc -c yywrap.c libmain.c && ar rcs libfl.a yywrap.o libmain.o)
cd src
export CFLAGS='-std=gnu89 -fcommon -Wno-return-type -Wno-implicit-function-declaration -Wno-implicit-int -Wno-int-conversion -Wno-incompatible-pointer-types'
export LDFLAGS="-L$PWD/../stubs"
make clean >/dev/null && make >../build.log 2>&1 && make install >>../build.log 2>&1
echo 'a)/nqrwpos' | MORPHLIB=../stemlib ../bin/cruncher -S
