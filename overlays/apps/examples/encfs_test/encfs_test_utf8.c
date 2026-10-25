/****************************************************************************
 * apps/examples/encfs_test/encfs_test_utf8.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/fs/encpathfs.h>
#include <nuttx/fs/ioctl.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "encfs_test_shared.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define UTF_ROOT "/data/utf8-acceptance"
#define UTF_DIR UTF_ROOT "/课程资料"
#define UTF_ARCHIVE UTF_ROOT "/资料归档"
#define UTF_ORIGINAL UTF_DIR "/密钥🔐.txt"
#define UTF_RENAMED UTF_ARCHIVE "/重命名-🔐.txt"
#define UTF_NFC UTF_ARCHIVE "/caf\xc3\xa9"
#define UTF_NFD UTF_ARCHIVE "/cafe\xcc\x81"
#define UTF_CONTENT "ENCFS_UTF8_PERSISTENT_CONTENT_20261024"

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static bool utf8_contents(const char *path, const char *expected)
{
  char buffer[64];
  ssize_t n;
  bool ok;
  int fd = open(path, O_RDONLY);

  if (fd < 0)
    {
      return false;
    }

  n = read(fd, buffer, sizeof(buffer));
  ok = n == strlen(expected) && memcmp(buffer, expected, n) == 0;
  if (close(fd) < 0)
    {
      ok = false;
    }

  return ok;
}

static bool utf8_create(const char *path, const char *contents)
{
  bool ok;
  int fd = open(path, O_CREAT | O_EXCL | O_WRONLY, 0666);

  if (fd < 0)
    {
      return false;
    }

  ok = write(fd, contents, strlen(contents)) == strlen(contents) &&
       fsync(fd) == 0;
  if (close(fd) < 0)
    {
      ok = false;
    }

  return ok;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void encfs_test_utf8_crypto(void)
{
  /* Boundary vectors from RFC 3629, plus Chinese, two-byte Latin and
   * supplementary-plane characters.  Invalid cases cover each prohibited
   * class, rather than deriving expected values from the C validator.
   */

  static const char * const valid[] =
  {
    "中文文件.txt", "🔐-café", "\xc2\x80", "\xdf\xbf",
    "\xe0\xa0\x80", "\xed\x9f\xbf", "\xee\x80\x80",
    "\xef\xbf\xbf", "\xf0\x90\x80\x80", "\xf4\x8f\xbf\xbf"
  };

  static const char * const invalid[] =
  {
    "\x80", "\xbf", "\xc0\x80", "\xc1\xbf", "\xc0\xaf",
    "\xe0\x80\xaf", "\xf0\x80\x80\xaf", "\xc2", "\xe4\xb8",
    "\xf0\x9f\x94", "\xe4!\xad", "\xed\xa0\x80",
    "\xed\xbf\xbf", "\xf4\x90\x80\x80", "\xf5\x80\x80\x80",
    "\xf8\x88\x80\x80\x80", "\xfe", "\xff", "a/b", "a\x7f", "a\n"
  };

  uint8_t key[ENCPATHFS_KEY_SIZE];
  char encoded[ENCPATHFS_ENCODED_MAX + 1];
  char decoded[ENCPATHFS_NAME_MAX + 1];
  char boundary[ENCPATHFS_NAME_MAX + 2];
  char label[64];
  size_t i;

  for (i = 0; i < sizeof(key); i++)
    {
      key[i] = i;
    }

  encfs_test_check("ASCII E1 disk-format compatibility vector",
      encpathfs_encode_name(key, "secret.txt", encoded,
                            sizeof(encoded)) == 0 &&
      strcmp(encoded, "E1_A1CP2UT8PNK4I9HJ7UQILMT155DE81L20K7VS8F3VO") == 0);
  for (i = 0; i < sizeof(valid) / sizeof(valid[0]); i++)
    {
      snprintf(label, sizeof(label), "UTF8 valid scalar/name vector %u",
               (unsigned int)i);
      encfs_test_check(label,
          encpathfs_encode_name(key, valid[i], encoded,
                                sizeof(encoded)) == 0 &&
          encpathfs_decode_name(key, encoded, decoded,
                                sizeof(decoded)) == 0 &&
          strcmp(valid[i], decoded) == 0);
    }

  for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++)
    {
      snprintf(label, sizeof(label), "UTF8 invalid sequence/name vector %u",
               (unsigned int)i);
      encfs_test_check(label,
          encpathfs_encode_name(key, invalid[i], encoded,
                                sizeof(encoded)) == -EINVAL);
    }

  for (i = 0; i < 32; i++)
    {
      memcpy(boundary + i * 4, "🔐", 4);
    }

  boundary[128] = '\0';
  encfs_test_check("UTF8 128-byte supplementary name round trip",
      encpathfs_encode_name(key, boundary, encoded, sizeof(encoded)) == 0 &&
      strlen(encoded) == ENCPATHFS_ENCODED_MAX &&
      encpathfs_decode_name(key, encoded, decoded, sizeof(decoded)) == 0 &&
      strcmp(boundary, decoded) == 0);
  boundary[128] = 'x';
  boundary[129] = '\0';
  encfs_test_check("UTF8 129-byte component rejected",
      encpathfs_encode_name(key, boundary, encoded,
                            sizeof(encoded)) == -ENAMETOOLONG);
  boundary[127] = '\0';
  encfs_test_check("UTF8 incomplete scalar at byte limit rejected",
      encpathfs_encode_name(key, boundary, encoded,
                            sizeof(encoded)) == -EINVAL);
  memset(key, 0, sizeof(key));
}

void encfs_test_utf8_filesystem(void)
{
  struct dirent *entry;
  struct stat st;
  char path[sizeof(UTF_ROOT) + ENCPATHFS_NAME_MAX + 2];
  char *logical;
  DIR *dir;
  size_t prefix;
  size_t i;
  int found = 0;
  int fd;
  bool ok;

  encfs_test_check("UTF8 create test directory", mkdir(UTF_ROOT, 0777) == 0);
  encfs_test_check("UTF8 create Chinese nested directory",
                  mkdir(UTF_DIR, 0777) == 0);
  encfs_test_check("UTF8 create, write and sync Chinese/emoji file",
                  utf8_create(UTF_ORIGINAL, UTF_CONTENT));
  encfs_test_check("UTF8 stat reports original content length",
                  stat(UTF_ORIGINAL, &st) == 0 &&
                  st.st_size == strlen(UTF_CONTENT));

  fd = open(UTF_ORIGINAL, O_RDONLY);
  encfs_test_check("UTF8 open existing file", fd >= 0);
  if (fd >= 0)
    {
      logical = malloc(PATH_MAX);
      encfs_test_check("UTF8 allocate logical FILEPATH buffer",
                      logical != NULL);
      if (logical != NULL)
        {
          encfs_test_check("UTF8 FILEPATH preserves exact logical bytes",
              ioctl(fd, FIOC_FILEPATH,
                    (unsigned long)(uintptr_t)logical) == 0 &&
              strcmp(logical, UTF_ORIGINAL) == 0);
          free(logical);
        }

      encfs_test_check("UTF8 close existing file", close(fd) == 0);
    }

  dir = opendir(UTF_DIR);
  encfs_test_check("UTF8 open Chinese directory", dir != NULL);
  if (dir != NULL)
    {
      while ((entry = readdir(dir)) != NULL)
        {
          found += strcmp(entry->d_name, "密钥🔐.txt") == 0;
        }

      encfs_test_check("UTF8 readdir restores exact Chinese/emoji bytes",
                      found == 1);
      encfs_test_check("UTF8 close Chinese directory", closedir(dir) == 0);
    }

  encfs_test_check("UTF8 rename whole Chinese directory",
                  rename(UTF_DIR, UTF_ARCHIVE) == 0);
  encfs_test_check("UTF8 rename mixed-name file",
                  rename(UTF_ARCHIVE "/密钥🔐.txt", UTF_RENAMED) == 0);
  encfs_test_check("UTF8 renamed file retains contents",
                  utf8_contents(UTF_RENAMED, UTF_CONTENT));
  encfs_test_check("UTF8 create composed accent name",
                  utf8_create(UTF_NFC, "NFC"));
  encfs_test_check("UTF8 create distinct decomposed accent name",
                  utf8_create(UTF_NFD, "NFD"));
  encfs_test_check("UTF8 normalization forms retain separate contents",
                  utf8_contents(UTF_NFC, "NFC") &&
                  utf8_contents(UTF_NFD, "NFD"));

  prefix = strlen(UTF_ROOT) + 1;
  memcpy(path, UTF_ROOT "/", prefix);
  for (i = 0; i < 32; i++)
    {
      memcpy(path + prefix + i * 4, "🔐", 4);
    }

  path[prefix + 128] = '\0';
  ok = utf8_create(path, "boundary") && utf8_contents(path, "boundary");
  encfs_test_check("UTF8 create/read 128-byte logical name", ok);
  encfs_test_check("UTF8 unlink 128-byte logical name", unlink(path) == 0);
  path[prefix + 128] = 'x';
  path[prefix + 129] = '\0';
  fd = open(path, O_CREAT | O_EXCL | O_WRONLY, 0666);
  encfs_test_check("UTF8 filesystem rejects 129-byte name",
                  fd < 0 && errno == ENAMETOOLONG);
  if (fd >= 0)
    {
      close(fd);
    }

  memcpy(path + prefix, "bad-\xed\xa0\x80", 8);
  fd = open(path, O_CREAT | O_EXCL | O_WRONLY, 0666);
  encfs_test_check("UTF8 filesystem rejects surrogate sequence",
                  fd < 0 && errno == EINVAL);
  if (fd >= 0)
    {
      close(fd);
    }

  /* Keep these three small files for the independent cold-boot mode. */
}

void encfs_test_utf8_verify(void)
{
  struct stat st;

  encfs_test_check("UTF8 persistent Chinese directory after reboot",
                  stat(UTF_ARCHIVE, &st) == 0 && S_ISDIR(st.st_mode));
  encfs_test_check("UTF8 persistent Chinese/emoji file after reboot",
                  utf8_contents(UTF_RENAMED, UTF_CONTENT));
  encfs_test_check("UTF8 persistent composed name after reboot",
                  utf8_contents(UTF_NFC, "NFC"));
  encfs_test_check("UTF8 persistent decomposed name after reboot",
                  utf8_contents(UTF_NFD, "NFD"));
}
