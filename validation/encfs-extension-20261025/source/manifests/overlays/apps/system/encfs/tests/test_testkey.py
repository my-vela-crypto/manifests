#!/usr/bin/env python3
"""Compile the production CLI and check its test-key input boundary on host."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


HARNESS = r'''
#include <nuttx/fs/encfs.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int encfs_cli_main(int argc, char *argv[]);

static int calls;
static int selected;
static unsigned char saved[ENCFS_MASTER_KEY_SIZE];
static int busy;
static unsigned int checks;

#define CHECK(expr) do { assert(expr); checks++; } while (0)

int encfs_start(void) { return 0; }
int encfs_stop(void) { return 0; }
int encfs_provision(void) { return 0; }
int encfs_get_status(struct encfs_status_s *status)
{
  memset(status, 0, sizeof(*status));
  return 0;
}

#ifdef CONFIG_ENCFS_TESTING
int encfs_test_set_master(const uint8_t *key, size_t keylen)
{
  calls++;
  if (busy)
    {
      return -EBUSY;
    }

  CHECK(key == NULL ? keylen == 0 : keylen == ENCFS_MASTER_KEY_SIZE);
  selected = key == NULL ? 2 : 1;
  memset(saved, 0, sizeof(saved));
  if (key != NULL)
    {
      memcpy(saved, key, sizeof(saved));
    }

  return 0;
}

int encfs_test_use_board_key(void)
{
  calls++;
  if (busy)
    {
      return -EBUSY;
    }

  selected = 0;
  memset(saved, 0, sizeof(saved));
  return 0;
}
#endif

static int command(const char *arg)
{
  char *argv[] = {"encfs", "testkey", (char *)arg};
  return encfs_cli_main(3, argv);
}

int main(void)
{
  const char *hex =
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
  char invalid[80];
  int before;
  unsigned int i;

#ifdef CONFIG_ENCFS_TESTING
  const unsigned char expected[] =
    {1, 35, 69, 103, 137, 171, 205, 239,
     1, 35, 69, 103, 137, 171, 205, 239,
     1, 35, 69, 103, 137, 171, 205, 239,
     1, 35, 69, 103, 137, 171, 205, 239};
  CHECK(command(hex) == EXIT_SUCCESS);
  CHECK(selected == 1 && memcmp(saved, expected, sizeof(saved)) == 0);
  CHECK(command("0123456789ABCDEF0123456789abcdef0123456789ABCDEF0123456789abcdef")
        == EXIT_SUCCESS);
  CHECK(memcmp(saved, expected, sizeof(saved)) == 0);

  /* Every short length and a long input must leave the selected key alone. */

  for (i = 0; i <= 65; i++)
    {
      if (i == 64)
        {
          continue;
        }

      memset(invalid, '1', i);
      invalid[i] = '\0';
      before = calls;
      CHECK(command(invalid) == EXIT_FAILURE);
      CHECK(calls == before);
      CHECK(selected == 1 && memcmp(saved, expected, sizeof(saved)) == 0);
    }

  /* Reject a non-hex character at every position, including the last. */

  for (i = 0; i < 64; i++)
    {
      strcpy(invalid, hex);
      invalid[i] = i & 1 ? 'g' : ' ';
      before = calls;
      CHECK(command(invalid) == EXIT_FAILURE);
      CHECK(calls == before);
      CHECK(selected == 1 && memcmp(saved, expected, sizeof(saved)) == 0);
    }

  busy = 1;
  CHECK(command("board") == EXIT_FAILURE);
  CHECK(command("none") == EXIT_FAILURE);
  CHECK(command(hex) == EXIT_FAILURE);
  CHECK(selected == 1 && memcmp(saved, expected, sizeof(saved)) == 0);
  busy = 0;
  CHECK(command("none") == EXIT_SUCCESS);
  CHECK(selected == 2);
  CHECK(command("board") == EXIT_SUCCESS);
  CHECK(selected == 0);

  {
    char *missing[] = {"encfs", "testkey"};
    char *extra[] = {"encfs", "testkey", "board", "extra"};
    before = calls;
    CHECK(encfs_cli_main(2, missing) == EXIT_FAILURE);
    CHECK(encfs_cli_main(4, extra) == EXIT_FAILURE);
    CHECK(calls == before);
  }
#else
  before = calls;
  for (i = 0; i < 3; i++)
    {
      const char *args[] = {hex, "board", "none"};
      CHECK(command(args[i]) == EXIT_FAILURE);
    }

  CHECK(calls == before && selected == 0 && saved[0] == 0 && busy == 0);
  invalid[0] = '\0';
  CHECK(invalid[0] == '\0');
#endif

  printf("ENCFS_CLI_TEST_RESULT checks=%u fail=0\n", checks);
  return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuttx", type=Path,
                        default=Path(__file__).resolve().parents[4] / "nuttx")
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    cli = Path(__file__).resolve().parents[1] / "encfs_main.c"
    header = args.nuttx / "include/nuttx/fs/encfs.h"

    with tempfile.TemporaryDirectory(prefix="encfs-cli-test-") as directory:
        root = Path(directory)
        (root / "nuttx/fs").mkdir(parents=True)
        shutil.copyfile(header, root / "nuttx/fs/encfs.h")
        (root / "nuttx/compiler.h").write_text("#define FAR\n")
        (root / "harness.c").write_text(HARNESS)
        for testing in (True, False):
            variant = "testing" if testing else "production"
            (root / "nuttx/config.h").write_text(
                "#define CONFIG_ENCFS_TESTING 1\n" if testing else "")
            common = [args.cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                      "-I", str(root)]
            subprocess.run(common + ["-Dmain=encfs_cli_main", "-c", str(cli),
                                     "-o", str(root / "cli.o")], check=True)
            executable = root / variant
            subprocess.run(common + [str(root / "harness.c"),
                                     str(root / "cli.o"), "-o", str(executable)],
                           check=True)
            result = subprocess.run([str(executable)], check=True,
                                    text=True, capture_output=True)
            # Never print or leak the entered key, including on errors.
            assert "0123456789abcdef" not in result.stdout + result.stderr
            assert "0123456789ABCDEF" not in result.stdout + result.stderr
            marker = next(line for line in result.stdout.splitlines()
                          if line.startswith("ENCFS_CLI_TEST_RESULT"))
            print(f"{variant}: {marker}")


if __name__ == "__main__":
    main()
