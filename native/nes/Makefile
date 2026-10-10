# Builds the NES emulator for the board: agnes (MIT, patched by agnes_flipdot.patch) + nes_flipdot.c.
# scripts/setup-nes.sh fetches the pinned agnes, applies the patch into build/src and runs this.

SRC = build/src
OUTPUT = build/nes_flipdot

CC ?= gcc
CFLAGS ?= -O2
CFLAGS += -I$(SRC)

all: $(OUTPUT)

$(OUTPUT): nes_flipdot.c $(SRC)/agnes.c $(SRC)/agnes.h
	$(CC) $(CFLAGS) -w -c $(SRC)/agnes.c -o build/agnes.o
	$(CC) $(CFLAGS) -Wall -c nes_flipdot.c -o build/nes_flipdot.o
	$(CC) $(CFLAGS) build/agnes.o build/nes_flipdot.o -o $@

clean:
	rm -f build/agnes.o build/nes_flipdot.o $(OUTPUT)

.PHONY: all clean
