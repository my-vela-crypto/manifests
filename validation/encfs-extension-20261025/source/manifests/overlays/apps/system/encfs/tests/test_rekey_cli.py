#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the actual CLI in wrapped production/test variants and test IDs."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile


APPS = Path(__file__).resolve().parents[3]
NUTTX = APPS.parent / "nuttx"

HARNESS = r'''
#include <nuttx/fs/encfs.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int encfs_cli_main(int argc, char **argv);
static unsigned int checks;
static unsigned int calls;
static uint32_t saved_id;
static int error;
static char saved_path[128];
static bool fail_commit;
#define CHECK(x) do {assert(x); checks++;} while (0)

int encfs_start(void) {return 0;}
int encfs_stop(void) {return 0;}
int encfs_provision(void) {return 0;}
int encfs_get_status(struct encfs_status_s *s)
{memset(s, 0, sizeof(*s)); return 0;}
int encfs_rekey(uint32_t id)
{calls++; saved_id = id; return error;}

#ifdef CONFIG_ENCFS_TESTING
int encfs_test_set_master(const uint8_t *k, size_t n)
{(void)k; (void)n; return 0;}
int encfs_test_use_board_key(void) {return 0;}
int encfs_test_use_keyfiles(const char *path)
{calls++; snprintf(saved_path, sizeof(saved_path), "%s", path); return error;}
int encfs_test_keyslot_fail_commit(bool enabled)
{calls++; fail_commit = enabled; return error;}
int encfs_test_keyslot_pause_commit(unsigned int milliseconds)
{calls++; saved_id = milliseconds; return error;}
#endif

static int command(const char *verb, const char *arg)
{
  char *argv[] = {"encfs", (char *)verb, (char *)arg};
  return encfs_cli_main(3, argv);
}

int main(void)
{
  const char *invalid[] = {"", "-1", "+1", " 1", "1 ", "1x", "0x1",
    "4294967296", "999999999999999999999999999999999999999"};
  unsigned int before;
  unsigned int i;

  CHECK(command("rekey", "0") == EXIT_SUCCESS && saved_id == 0);
  CHECK(command("rekey", "1") == EXIT_SUCCESS && saved_id == 1);
  CHECK(command("rekey", "4294967295") == EXIT_SUCCESS &&
        saved_id == UINT32_MAX);
  for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++)
    {
      before = calls;
      CHECK(command("rekey", invalid[i]) == EXIT_FAILURE);
      CHECK(calls == before);
    }

  error = -EIO;
  CHECK(command("rekey", "2") == EXIT_FAILURE && saved_id == 2);
  error = 0;
  {
    char *missing[] = {"encfs", "rekey"};
    char *extra[] = {"encfs", "rekey", "1", "extra"};
    before = calls;
    CHECK(encfs_cli_main(2, missing) == EXIT_FAILURE);
    CHECK(encfs_cli_main(4, extra) == EXIT_FAILURE);
    CHECK(calls == before);
  }

  before = calls;
#ifdef CONFIG_ENCFS_TESTING
  CHECK(command("keyfiles", "/share/keys") == EXIT_SUCCESS);
  CHECK(strcmp(saved_path, "/share/keys") == 0 && calls == before + 1);
  CHECK(command("keyslot-failcommit", "on") == EXIT_SUCCESS && fail_commit);
  CHECK(command("keyslot-failcommit", "off") == EXIT_SUCCESS && !fail_commit);
  before = calls;
  CHECK(command("keyslot-failcommit", "yes") == EXIT_FAILURE);
  CHECK(calls == before);
  CHECK(command("keyslot-pausecommit", "60000") == EXIT_SUCCESS &&
        saved_id == 60000);
  before = calls;
  CHECK(command("keyslot-pausecommit", "60001") == EXIT_FAILURE);
  CHECK(calls == before);
#else
  CHECK(command("keyfiles", "/share/keys") == EXIT_FAILURE);
  CHECK(command("keyslot-failcommit", "on") == EXIT_FAILURE);
  CHECK(calls == before && saved_path[0] == 0 && !fail_commit);
#endif
  printf("ENCFS_REKEY_CLI_RESULT checks=%u fail=0\n", checks);
  return 0;
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="encfs-rekey-cli-") as name:
        root = Path(name)
        (root / "nuttx/fs").mkdir(parents=True)
        shutil.copyfile(NUTTX / "include/nuttx/fs/encfs.h",
                        root / "nuttx/fs/encfs.h")
        (root / "nuttx/compiler.h").write_text("#define FAR\n#define CODE\n")
        (root / "test.c").write_text(HARNESS)
        for testing in (False, True):
            variant = "testing" if testing else "production"
            (root / "nuttx/config.h").write_text(
                "#define CONFIG_ENCFS_KEYWRAP 1\n" +
                ("#define CONFIG_ENCFS_TESTING 1\n" if testing else ""))
            common = [os.environ.get("CC", "cc"), "-std=c99", "-Wall",
                      "-Wextra", "-Werror", "-I", str(root)]
            subprocess.run(common + ["-Dmain=encfs_cli_main", "-c",
                                     str(APPS / "system/encfs/encfs_main.c"),
                                     "-o", str(root / "cli.o")], check=True)
            executable = root / variant
            subprocess.run(common + [str(root / "test.c"),
                                     str(root / "cli.o"), "-o", str(executable)],
                           check=True)
            result = subprocess.run([str(executable)], check=True,
                                    text=True, capture_output=True)
            print(variant + ": " + next(x for x in result.stdout.splitlines()
                                        if x.startswith("ENCFS_REKEY_CLI_RESULT")))


if __name__ == "__main__":
    main()
