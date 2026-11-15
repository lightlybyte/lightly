# Re:G 1010 usermode driver - Makefile
# Builds the core driver library and a test program.
# Works on Linux (make) and Windows (mingw32-make).

CC      ?= gcc
CFLAGS  := -Wall -Wextra -O2 -std=c11 -Icommon
LDFLAGS :=
LDLIBS  := -lm

# Detect platform
UNAME_S := $(shell uname -s 2>/dev/null || echo Windows_NT)
ifeq ($(UNAME_S),Windows_NT)
    PLATFORM_SRC := platform/windows/reg10_platform.c
    EXE := .exe
else
    PLATFORM_SRC := platform/linux/reg10_platform.c
    EXE :=
endif

CORE_SRC := \
	common/reg10_mem.c \
	common/reg10_cpu.c \
	common/reg10_driver.c \
	common/reg10_asm.c \
	$(PLATFORM_SRC)

LIB := libreg10.a
TEST := reg10_test$(EXE)
ASM_TOOL := reg10_as$(EXE)

.PHONY: all clean test asm

all: $(LIB)

$(LIB): $(CORE_SRC:.c=.o)
	ar rcs $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

test: $(LIB)
	$(CC) $(CFLAGS) -o $(TEST) test/reg10_test.c $(LIB) $(LDLIBS)

asm: reg10_asm.c
	$(CC) $(CFLAGS) -o $(ASM_TOOL) common/reg10_asm.c $(LDLIBS)

clean:
	rm -f common/*.o platform/*/*.o $(LIB) $(TEST) $(ASM_TOOL)
