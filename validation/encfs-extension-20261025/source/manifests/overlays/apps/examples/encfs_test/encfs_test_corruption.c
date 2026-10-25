/****************************************************************************
 * apps/examples/encfs_test/encfs_test_corruption.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/fs/encfs.h>
#include <nuttx/fs/encpathfs.h>

#include <sys/mount.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "encfs_test_shared.h"

/* The host runner gives this mode a NEW, dedicated blank data image.  The
 * raw parent below is reformatted between fixtures.  Never run this mode
 * against the write/verify image or an existing user data image.
 */

#define CORRUPTION_META       "/datap/.encfs.meta"
#define CORRUPTION_ROOT       "/datap/.p"
#define CORRUPTION_OPTIONS    "lower=/datap,root=.p,slot=0"
#define CORRUPTION_META_SIZE  51

struct corruption_fixture_s
{
  uint8_t key[ENCPATHFS_KEY_SIZE];
  bool lower;
  bool upper;
};

enum corruption_meta_e
{
  CORRUPTION_META_MISSING = 0,
  CORRUPTION_META_MAGIC,
  CORRUPTION_META_VERSION,
  CORRUPTION_META_SHORT,
  CORRUPTION_META_LONG,
  CORRUPTION_META_TAG,
  CORRUPTION_META_CIPHERTEXT,
  CORRUPTION_META_DIRECTORY,
  CORRUPTION_ROOT_MISSING,
  CORRUPTION_ROOT_FILE
};

static const struct
{
  const char *name;
  int error;
} g_corruption_meta[] =
{
  {"missing metadata", ENOENT},
  {"wrong metadata magic", EPROTONOSUPPORT},
  {"unsupported metadata version", EPROTONOSUPPORT},
  {"truncated metadata", EINVAL},
  {"overlong metadata", EINVAL},
  {"tampered metadata tag", EBADMSG},
  {"tampered metadata ciphertext", EBADMSG},
  {"metadata is a directory", EINVAL},
  {"missing physical root", ENOENT},
  {"physical root is a file", ENOTDIR}
};

static void corruption_check(const char *fixture, const char *operation,
                             bool passed)
{
  char label[160];

  snprintf(label, sizeof(label), "corruption %s: %s", fixture, operation);
  encfs_test_check(label, passed);
}

static bool corruption_write(const char *path, const void *buffer,
                             size_t length)
{
  const uint8_t *bytes = buffer;
  size_t offset = 0;
  ssize_t count;
  bool good = true;
  int fd;

  fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
  if (fd < 0)
    {
      return false;
    }

  while (offset < length)
    {
      count = write(fd, bytes + offset, length - offset);
      if (count <= 0)
        {
          good = false;
          break;
        }

      offset += count;
    }

  if (good && fsync(fd) < 0)
    {
      good = false;
    }

  if (close(fd) < 0)
    {
      good = false;
    }

  return good;
}

static bool corruption_read(const char *path, void *buffer, size_t length)
{
  uint8_t *bytes = buffer;
  uint8_t extra;
  size_t offset = 0;
  ssize_t count;
  bool good = true;
  int fd;

  fd = open(path, O_RDONLY);
  if (fd < 0)
    {
      return false;
    }

  while (offset < length)
    {
      count = read(fd, bytes + offset, length - offset);
      if (count <= 0)
        {
          good = false;
          break;
        }

      offset += count;
    }

  if (good && read(fd, &extra, 1) != 0)
    {
      good = false;
    }

  if (close(fd) < 0)
    {
      good = false;
    }

  return good;
}

static bool corruption_finish(struct corruption_fixture_s *fixture,
                              const char *name)
{
  int ret;
  bool good = true;

  if (fixture->upper)
    {
      ret = umount(ENCFS_UPPER_PATH);
      corruption_check(name, "unmount upper and owned lower", ret == 0);
      if (ret == 0)
        {
          fixture->upper = false;
        }
      else
        {
          good = false;
        }
    }

  if (fixture->lower)
    {
      ret = encpathfs_recover();
      corruption_check(name, "no pending metadata handle", ret == 0);
      if (ret < 0)
        {
          good = false;
        }

      ret = umount(ENCFS_LOWER_PATH);
      corruption_check(name, "unmount visible lower", ret == 0);
      if (ret == 0)
        {
          fixture->lower = false;
        }
      else
        {
          good = false;
        }
    }

  ret = encpathfs_clearkey(0);
  corruption_check(name, "slot is idle after cleanup", ret == 0);
  return good && ret == 0;
}

static bool corruption_begin(struct corruption_fixture_s *fixture,
                             const char *name)
{
  int ret;

  if (fixture->upper || fixture->lower)
    {
      corruption_check(name, "previous fixture released mounts", false);
      return false;
    }

  ret = mount(ENCFS_PARENT_DEVICE, ENCFS_LOWER_PATH, "fatfs", 0,
              "forceformat");
  corruption_check(name, "format dedicated raw exFAT fixture", ret == 0);
  if (ret < 0)
    {
      return false;
    }

  fixture->lower = true;
  ret = encpathfs_initialize(ENCFS_LOWER_PATH, fixture->key,
                           sizeof(fixture->key));
  corruption_check(name, "initialize authenticated volume", ret == 0);
  return ret == 0;
}

static bool corruption_mount(struct corruption_fixture_s *fixture,
                             const char *name)
{
  int ret = encpathfs_setkey(0, fixture->key, sizeof(fixture->key));

  corruption_check(name, "stage restored key", ret == 0);
  if (ret < 0)
    {
      return false;
    }

  ret = mount(NULL, ENCFS_UPPER_PATH, "encpathfs", 0,
              CORRUPTION_OPTIONS);
  corruption_check(name, "mount authenticated upper", ret == 0);
  if (ret == 0)
    {
      fixture->lower = false;
      fixture->upper = true;
    }

  return ret == 0;
}

static bool corruption_reject(struct corruption_fixture_s *fixture,
                              const char *name, int expected)
{
  struct stat st;
  static const char probe[] = "lower-still-usable-after-rejected-mount";
  char buffer[sizeof(probe)];
  bool good;
  int ret;
  int error;

  ret = encpathfs_setkey(0, fixture->key, sizeof(fixture->key));
  corruption_check(name, "stage key before rejected bind", ret == 0);
  if (ret < 0)
    {
      return false;
    }

  errno = 0;
  ret = mount(NULL, ENCFS_UPPER_PATH, "encpathfs", 0,
              CORRUPTION_OPTIONS);
  error = errno;
  corruption_check(name, "reject with specified errno",
                   ret < 0 && error == expected);
  if (ret == 0)
    {
      fixture->upper = true;
      fixture->lower = false;
      return false;
    }

  good = error == expected;
  errno = 0;
  ret = mount(NULL, ENCFS_UPPER_PATH, "encpathfs", 0,
              CORRUPTION_OPTIONS);
  error = errno;
  corruption_check(name, "failed bind consumes staged key",
                   ret < 0 && error == ENOKEY);
  if (ret == 0)
    {
      fixture->upper = true;
      fixture->lower = false;
      return false;
    }

  good = good && error == ENOKEY;
  ret = encpathfs_setkey(0, fixture->key, sizeof(fixture->key));
  corruption_check(name, "failed bind releases slot lock", ret == 0);
  good = good && ret == 0;
  ret = encpathfs_clearkey(0);
  corruption_check(name, "clear re-staged slot", ret == 0);
  good = good && ret == 0;

  ret = stat(ENCFS_LOWER_PATH, &st);
  corruption_check(name, "rejected bind leaves lower visible",
                   ret == 0 && S_ISDIR(st.st_mode));
  good = good && ret == 0 && S_ISDIR(st.st_mode);
  ret = corruption_write("/datap/recovery-probe", probe, sizeof(probe));
  corruption_check(name, "write through lower after rejected bind", ret);
  good = good && ret;
  ret = corruption_read("/datap/recovery-probe", buffer, sizeof(buffer));
  corruption_check(name, "read through lower after rejected bind",
                   ret && memcmp(buffer, probe, sizeof(probe)) == 0);
  good = good && ret && memcmp(buffer, probe, sizeof(probe)) == 0;
  ret = unlink("/datap/recovery-probe");
  corruption_check(name, "remove lower recovery probe", ret == 0);
  return good && ret == 0;
}

static bool corruption_metadata(struct corruption_fixture_s *fixture,
                                enum corruption_meta_e which)
{
  const char *name = g_corruption_meta[which].name;
  uint8_t original[CORRUPTION_META_SIZE];
  uint8_t modified[CORRUPTION_META_SIZE + 1];
  size_t length = sizeof(original);
  bool changed;
  bool good;
  int ret;

  if (!corruption_begin(fixture, name))
    {
      return false;
    }

  good = corruption_read(CORRUPTION_META, original, sizeof(original));
  corruption_check(name, "backup exact metadata record", good);
  if (!good)
    {
      return false;
    }

  memcpy(modified, original, sizeof(original));
  modified[sizeof(original)] = 0;
  switch (which)
    {
      case CORRUPTION_META_MISSING:
        changed = unlink(CORRUPTION_META) == 0;
        break;

      case CORRUPTION_META_DIRECTORY:
        changed = unlink(CORRUPTION_META) == 0 &&
                  mkdir(CORRUPTION_META, 0700) == 0;
        break;

      case CORRUPTION_ROOT_MISSING:
        changed = rmdir(CORRUPTION_ROOT) == 0;
        break;

      case CORRUPTION_ROOT_FILE:
        changed = rmdir(CORRUPTION_ROOT) == 0 &&
                  corruption_write(CORRUPTION_ROOT, "x", 1);
        break;

      default:
        if (which == CORRUPTION_META_MAGIC)
          {
            modified[0] ^= 1;
          }
        else if (which == CORRUPTION_META_VERSION)
          {
            modified[8]++;
          }
        else if (which == CORRUPTION_META_SHORT)
          {
            length--;
          }
        else if (which == CORRUPTION_META_LONG)
          {
            length++;
          }
        else if (which == CORRUPTION_META_TAG)
          {
            modified[16] ^= 1;
          }
        else
          {
            modified[sizeof(original) - 1] ^= 1;
          }

        changed = corruption_write(CORRUPTION_META, modified, length);
        break;
    }

  corruption_check(name, "inject physical fixture damage", changed);
  if (!changed)
    {
      return false;
    }

  good = corruption_reject(fixture, name, g_corruption_meta[which].error);
  if (fixture->upper)
    {
      return false;
    }

  if (which == CORRUPTION_META_DIRECTORY)
    {
      ret = rmdir(CORRUPTION_META);
      corruption_check(name, "remove metadata directory fixture", ret == 0);
      good = good && ret == 0;
    }

  if (which == CORRUPTION_ROOT_FILE)
    {
      ret = unlink(CORRUPTION_ROOT);
      corruption_check(name, "remove physical root file fixture", ret == 0);
      good = good && ret == 0;
    }

  if (which == CORRUPTION_ROOT_MISSING || which == CORRUPTION_ROOT_FILE)
    {
      ret = mkdir(CORRUPTION_ROOT, 0700);
      corruption_check(name, "restore physical root directory", ret == 0);
      good = good && ret == 0;
    }

  ret = corruption_write(CORRUPTION_META, original, sizeof(original));
  corruption_check(name, "restore original authenticated metadata", ret);
  good = good && ret;
  if (!corruption_mount(fixture, name))
    {
      return false;
    }

  return corruption_finish(fixture, name) && good;
}

static bool corruption_directory(struct corruption_fixture_s *fixture,
                                 const char *name, const char *physical)
{
  char path[sizeof(CORRUPTION_ROOT) + ENCPATHFS_ENCODED_MAX + 3];
  struct dirent *entry;
  struct stat st;
  DIR *dir;
  bool good;
  int ret;
  int error;

  if (!corruption_begin(fixture, name))
    {
      return false;
    }

  snprintf(path, sizeof(path), "%s/%s", CORRUPTION_ROOT, physical);
  good = corruption_write(path, "injected physical directory entry",
                           sizeof("injected physical directory entry") - 1);
  corruption_check(name, "create sole injected physical entry", good);
  if (!good || !corruption_mount(fixture, name))
    {
      return false;
    }

  errno = 0;
  ret = stat(CORRUPTION_ROOT, &st);
  corruption_check(name, "upper hides injected lower namespace", ret < 0);
  good = good && ret < 0;
  dir = opendir("/data///");
  corruption_check(name, "opendir upper with repeated separators",
                   dir != NULL);
  if (dir == NULL)
    {
      return false;
    }

  errno = 0;
  entry = readdir(dir);
  error = errno;
  corruption_check(name, "readdir rejects malformed entry with EBADMSG",
                   entry == NULL && error == EBADMSG);
  good = good && entry == NULL && error == EBADMSG;
  corruption_check(name, "physical name is never returned", entry == NULL);
  ret = encpathfs_setkey(0, fixture->key, sizeof(fixture->key));
  corruption_check(name, "mounted corruption test retains slot lock",
                   ret == -EBUSY);
  good = good && ret == -EBUSY;

  rewinddir(dir);
  errno = 0;
  entry = readdir(dir);
  error = errno;
  corruption_check(name, "rewind rechecks corrupt entry",
                   entry == NULL && error == EBADMSG);
  good = good && entry == NULL && error == EBADMSG;
  errno = 0;
  ret = umount(ENCFS_UPPER_PATH);
  error = errno;
  corruption_check(name, "open corrupt directory still blocks unmount",
                   ret < 0 && error == EBUSY);
  good = good && ret < 0 && error == EBUSY;
  if (ret == 0)
    {
      fixture->upper = false;
    }

  ret = closedir(dir);
  corruption_check(name, "close corrupt directory", ret == 0);
  good = good && ret == 0;
  if (!corruption_finish(fixture, name))
    {
      return false;
    }

  ret = mount(ENCFS_PARENT_DEVICE, ENCFS_LOWER_PATH, "fatfs", 0, NULL);
  corruption_check(name, "remount lower after readdir rejection", ret == 0);
  if (ret < 0)
    {
      return false;
    }

  fixture->lower = true;
  ret = stat(path, &st);
  corruption_check(name, "readdir rejection preserves physical entry",
                   ret == 0);
  good = good && ret == 0;
  ret = unlink(path);
  corruption_check(name, "remove only injected entry", ret == 0);
  good = good && ret == 0;
  if (!corruption_mount(fixture, name))
    {
      return false;
    }

  dir = opendir(ENCFS_UPPER_PATH);
  corruption_check(name, "reopen repaired logical directory", dir != NULL);
  if (dir != NULL)
    {
      errno = 0;
      entry = readdir(dir);
      error = errno;
      corruption_check(name, "repaired directory has clean EOF",
                       entry == NULL && error == 0);
      good = good && entry == NULL && error == 0;
      ret = closedir(dir);
      corruption_check(name, "close repaired directory", ret == 0);
      good = good && ret == 0;
    }
  else
    {
      good = false;
    }

  return corruption_finish(fixture, name) && good;
}

static bool corruption_separators(struct corruption_fixture_s *fixture)
{
  const char *name = "repeated path separators";
  const char payload[] = "canonical-logical-file";
  char buffer[sizeof(payload)];
  struct dirent *entry;
  DIR *dir;
  bool good;
  int ret;

  if (!corruption_begin(fixture, name) ||
      !corruption_mount(fixture, name))
    {
      return false;
    }

  ret = mkdir("/data///repeated//", 0700);
  corruption_check(name, "mkdir with repeated and trailing separators",
                   ret == 0);
  good = ret == 0;
  ret = corruption_write("/data//repeated///ok", payload, sizeof(payload));
  corruption_check(name, "create through repeated separators", ret);
  good = good && ret;
  ret = corruption_read("/data/repeated/ok", buffer, sizeof(buffer));
  corruption_check(name, "canonical path addresses the same file",
                   ret && memcmp(buffer, payload, sizeof(payload)) == 0);
  good = good && ret && memcmp(buffer, payload, sizeof(payload)) == 0;
  dir = opendir("/data///repeated//");
  corruption_check(name, "opendir repeated separators", dir != NULL);
  if (dir != NULL)
    {
      entry = readdir(dir);
      corruption_check(name, "valid readdir returns logical name",
                       entry != NULL && strcmp(entry->d_name, "ok") == 0);
      good = good && entry != NULL && strcmp(entry->d_name, "ok") == 0;
      errno = 0;
      entry = readdir(dir);
      corruption_check(name, "valid directory ends without error",
                       entry == NULL && errno == 0);
      good = good && entry == NULL && errno == 0;
      ret = closedir(dir);
      corruption_check(name, "close separator directory", ret == 0);
      good = good && ret == 0;
    }
  else
    {
      good = false;
    }

  ret = unlink("/data//repeated///ok");
  corruption_check(name, "unlink through repeated separators", ret == 0);
  good = good && ret == 0;
  ret = rmdir("/data///repeated//");
  corruption_check(name, "rmdir through repeated separators", ret == 0);
  good = good && ret == 0;
  return corruption_finish(fixture, name) && good;
}

void encfs_test_corruption(void)
{
  static const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUV";
  struct corruption_fixture_s fixture;
  struct encfs_status_s status;
  char encoded[ENCPATHFS_ENCODED_MAX + 2];
  const char *last;
  size_t length;
  size_t i;
  bool good;
  int ret;

  memset(&fixture, 0, sizeof(fixture));
  for (i = 0; i < sizeof(fixture.key); i++)
    {
      fixture.key[i] = i + 1;
    }

  printf("ENCFS_CORRUPTION_FIXTURE dedicated blank raw disk; destructive\n");
  /* Refuse an existing live chain instead of stopping and reformatting it.
   * An already stopped encrypted image cannot be distinguished from a new
   * blank image through the path API: the host runner must verify its new
   * fixture before this destructive test is invoked.
   */

  ret = encfs_get_status(&status);
  good = ret == 0 && status.state == ENCFS_STOPPED &&
         !status.block_registered && !status.lower_visible &&
         !status.upper_mounted && !status.path_key_staged;
  corruption_check("setup", "refuse any existing live managed chain", good);
  if (!good)
    {
      return;
    }

  ret = encfs_stop();
  corruption_check("setup", "stop is idempotent on dedicated blank fixture",
                   ret == 0);
  if (ret < 0)
    {
      return;
    }

  for (i = 0; i < sizeof(g_corruption_meta) / sizeof(g_corruption_meta[0]); i++)
    {
      good = corruption_metadata(&fixture, i);
      if (!good)
        {
          goto cleanup;
        }
    }

  good = corruption_directory(&fixture, "plaintext directory entry",
                              "physical-plaintext-name");
  if (!good)
    {
      goto cleanup;
    }

  good = corruption_directory(&fixture, "invalid Base32 directory entry",
                              "E1_!INVALID_BASE32_DIRECTORY_ENTRY");
  if (!good)
    {
      goto cleanup;
    }

  ret = encpathfs_encode_name(fixture.key, "x", encoded, sizeof(encoded));
  corruption_check("canonical authenticated entry", "encode reference",
                   ret == 0);
  if (ret < 0)
    {
      goto cleanup;
    }

  encoded[3] = encoded[3] == '0' ? '1' : '0';
  good = corruption_directory(&fixture, "canonical tampered directory entry",
                              encoded);
  if (!good)
    {
      goto cleanup;
    }

  ret = encpathfs_encode_name(fixture.key, "x", encoded, sizeof(encoded));
  corruption_check("noncanonical padding bits", "encode reference", ret == 0);
  if (ret < 0)
    {
      goto cleanup;
    }

  length = strlen(encoded);
  last = strchr(alphabet, encoded[length - 1]);
  if (last == NULL || last - alphabet >= 31)
    {
      corruption_check("noncanonical padding bits", "find final symbol",
                       false);
      goto cleanup;
    }

  /* A one-byte logical name seals to 17 bytes, hence the last Base32 symbol
   * has four zero padding bits.  Alter only an unused bit, not ciphertext.
   */

  encoded[length - 1] = alphabet[last - alphabet + 1];
  good = corruption_directory(&fixture, "noncanonical unused Base32 bits",
                              encoded);
  if (!good)
    {
      goto cleanup;
    }

  ret = encpathfs_encode_name(fixture.key, "lowercase", encoded,
                             sizeof(encoded));
  corruption_check("lowercase Base32 entry", "encode reference", ret == 0);
  if (ret < 0)
    {
      goto cleanup;
    }

  for (i = 3; encoded[i] != '\0'; i++)
    {
      if (encoded[i] >= 'A' && encoded[i] <= 'V')
        {
          encoded[i] += 'a' - 'A';
          break;
        }
    }

  corruption_check("lowercase Base32 entry", "lowercase an alphabet symbol",
                   encoded[i] != '\0');
  if (encoded[i] == '\0' ||
      !corruption_directory(&fixture, "noncanonical lowercase Base32",
                             encoded))
    {
      goto cleanup;
    }

  ret = encpathfs_encode_name(fixture.key, "x", encoded, sizeof(encoded));
  corruption_check("padded Base32 entry", "encode reference", ret == 0);
  if (ret < 0)
    {
      goto cleanup;
    }

  strcat(encoded, "=");
  if (!corruption_directory(&fixture, "forbidden Base32 padding", encoded))
    {
      goto cleanup;
    }

  corruption_separators(&fixture);

cleanup:
  corruption_finish(&fixture, "final cleanup");
  ret = encfs_stop();
  corruption_check("final cleanup", "manager stays stopped", ret == 0);
  ret = encfs_get_status(&status);
  corruption_check("final cleanup", "no managed resource remains",
                   ret == 0 && status.state == ENCFS_STOPPED &&
                   !status.block_registered && !status.block_keyed &&
                   !status.lower_visible && !status.upper_mounted &&
                   !status.path_key_staged);
  corruption_check("final cleanup", "no fixture mount remains",
                   !fixture.lower && !fixture.upper);
  corruption_check("final cleanup", "no encrypted block node remains",
                   access(ENCFS_BLOCK_DEVICE, F_OK) < 0);
  memset(fixture.key, 0, sizeof(fixture.key));
}
