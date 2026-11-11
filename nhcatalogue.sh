#!/bin/sh
# Relocatable launcher: resolve this script's own directory, then run the
# nhcatalogue binary in the sibling nhcatalogue/ folder. Works no matter where
# the tarball is extracted.
exec "$(dirname "$(readlink -f "$0")")/nhcatalogue/nhcatalogue" "$@"
