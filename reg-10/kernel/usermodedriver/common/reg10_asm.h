#ifndef REG10_ASM_H
#define REG10_ASM_H

#include "reg10_isa.h"
#include <stddef.h>

/* Assemble a source string into a binary image of 64-bit instructions.
 * Returns number of words written (or -1 on error).
 * Output buffer must hold at least max_words * sizeof(uint64_t) bytes.
 */
int reg10_assemble(const char *src, uint64_t *out, size_t max_words,
                   char *errbuf, size_t errbuf_size);

/* Assemble from file */
int reg10_assemble_file(const char *path, uint64_t *out, size_t max_words,
                        char *errbuf, size_t errbuf_size);

#endif