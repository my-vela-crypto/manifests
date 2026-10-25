/****************************************************************************
 * apps/examples/encfs_test/encfs_test_extended.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/fs/encpathfs.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "encfs_test_shared.h"

#define EXT_ROOT "/data/acceptance-extended"
#define EXT_FILE EXT_ROOT "/attributes"
#define EXP_ROOT "/data/exb"
#define STRESS_REQUESTED 1000
#define STRESS_MAX 8191

/* FatFs currently accepts ordinary permission bits without persisting them;
 * stat reports 0777 and UID/GID changes are unsupported.  libc masks special
 * mode bits before dispatching chmod/fchmod to the filesystem.
 * Its only persisted chstat field is mtime, in two-second UTC FAT units.
 * These checks deliberately describe that contract rather than POSIX ACLs.
 */

static bool extended_write(int fd, const uint8_t *data, size_t size)
{
  ssize_t ret;
  size_t done = 0;

  while (done < size)
    {
      ret = write(fd, data + done, size - done);
      if (ret < 0 && errno == EINTR)
        {
          continue;
        }

      if (ret <= 0)
        {
          return false;
        }

      done += ret;
    }

  return true;
}

static bool extended_read(int fd, uint8_t *data, size_t size)
{
  uint8_t extra;
  ssize_t ret;
  size_t done = 0;

  while (done < size)
    {
      ret = read(fd, data + done, size - done);
      if (ret < 0 && errno == EINTR)
        {
          continue;
        }

      if (ret <= 0)
        {
          return false;
        }

      done += ret;
    }

  do
    {
      ret = read(fd, &extra, 1);
    }
  while (ret < 0 && errno == EINTR);

  return ret == 0;
}

static bool extended_contents(const char *path, const uint8_t *expected,
                              uint8_t *buffer, size_t length)
{
  bool ok;
  int fd = open(path, O_RDONLY);

  if (fd < 0)
    {
      return false;
    }

  ok = extended_read(fd, buffer, length) &&
       memcmp(buffer, expected, length) == 0;
  if (close(fd) < 0)
    {
      ok = false;
    }

  return ok;
}

static void extended_attributes(void)
{
  struct timespec times[2];
  struct stat st;
  char *path;
  int fd;

  fd = open(EXT_FILE, O_CREAT | O_EXCL | O_RDWR, 0666);
  encfs_test_check("extended create attribute sample", fd >= 0);
  if (fd < 0)
    {
      return;
    }

  encfs_test_check("attribute sample write and sync",
                  write(fd, "attribute", 9) == 9 && fsync(fd) == 0);
  encfs_test_check("chmod passes ordinary FatFs mode bits",
                  chmod(EXT_FILE, 0444) == 0);
  encfs_test_check("chmod retains FatFs reported mode",
                  stat(EXT_FILE, &st) == 0 &&
                  (st.st_mode & 0777) == 0777 && S_ISREG(st.st_mode));
  encfs_test_check("fchmod passes ordinary FatFs mode bits",
                  fchmod(fd, 0600) == 0);
  encfs_test_check("fchmod retains FatFs reported mode",
                  fstat(fd, &st) == 0 && (st.st_mode & 0777) == 0777);
  encfs_test_check("chmod masks unsupported special mode bits",
                  chmod(EXT_FILE, S_ISUID | 0600) == 0 &&
                  stat(EXT_FILE, &st) == 0 && !(st.st_mode & S_ISUID));
  encfs_test_check("fchmod masks unsupported special mode bits",
                  fchmod(fd, S_ISGID | 0600) == 0 &&
                  fstat(fd, &st) == 0 && !(st.st_mode & S_ISGID));
  encfs_test_check("chown propagates FatFs unsupported owner",
                  chown(EXT_FILE, 1, 1) < 0 && errno == EPERM);
  encfs_test_check("fchown propagates FatFs unsupported owner",
                  fchown(fd, 1, 1) < 0 && errno == EPERM);

  /* Flush writes before changing the directory entry timestamp, so f_sync
   * does not replace the explicit timestamp with a pending write timestamp.
   */

  times[0].tv_sec = 1700000000;
  times[0].tv_nsec = 0;
  times[1].tv_sec = 1700000003;
  times[1].tv_nsec = 0;
  encfs_test_check("utimensat reaches translated lower path",
                  utimensat(AT_FDCWD, EXT_FILE, times, 0) == 0);
  encfs_test_check("path mtime preserves FAT two-second precision",
                  stat(EXT_FILE, &st) == 0 &&
                  st.st_mtime == times[1].tv_sec - 1 &&
                  st.st_atime == st.st_mtime);
  times[1].tv_sec = 1700000126;
  encfs_test_check("futimens reaches translated open file",
                  futimens(fd, times) == 0);
  encfs_test_check("fd mtime reflects FAT timestamp",
                  fstat(fd, &st) == 0 &&
                  st.st_mtime == times[1].tv_sec);

  path = malloc(PATH_MAX);
  encfs_test_check("allocate FILEPATH buffer", path != NULL);
  if (path != NULL)
    {
      memset(path, 0, PATH_MAX);
      encfs_test_check("FILEPATH returns logical upper path",
                      ioctl(fd, FIOC_FILEPATH,
                            (unsigned long)(uintptr_t)path) == 0 &&
                      strcmp(path, EXT_FILE) == 0);
      encfs_test_check("FILEPATH hides lower mount and encoded components",
                      strstr(path, "/datap") == NULL &&
                      strstr(path, "/.p/") == NULL &&
                      strstr(path, "E1_") == NULL);
      free(path);
    }

  encfs_test_check("FILEPATH rejects null output",
                  ioctl(fd, FIOC_FILEPATH, 0UL) < 0 && errno == EINVAL);
  encfs_test_check("upper layer blocks device flush ioctl",
                  ioctl(fd, BIOC_FLUSH, 0UL) < 0 && errno == ENOTTY);
  encfs_test_check("upper layer rejects unknown ioctl",
                  ioctl(fd, _FIOC(0xff), 0UL) < 0 && errno == ENOTTY);
  encfs_test_check("close attribute sample", close(fd) == 0);
}

static void extended_case_names(void)
{
  static const char upper[] = EXT_ROOT "/CaseSensitive";
  static const char lower[] = EXT_ROOT "/casesensitive";
  uint8_t buffer[8];
  struct dirent *entry;
  DIR *dir;
  int upper_count = 0;
  int lower_count = 0;
  int fd;
  bool ok;

  fd = open(upper, O_CREAT | O_EXCL | O_WRONLY, 0666);
  encfs_test_check("create mixed-case logical name", fd >= 0);
  if (fd >= 0)
    {
      ok = write(fd, "UPPER", 5) == 5;
      if (close(fd) < 0)
        {
          ok = false;
        }

      encfs_test_check("write mixed-case logical name", ok);
    }

  fd = open(lower, O_CREAT | O_EXCL | O_WRONLY, 0666);
  encfs_test_check("create distinct lowercase logical name", fd >= 0);
  if (fd >= 0)
    {
      ok = write(fd, "lower", 5) == 5;
      if (close(fd) < 0)
        {
          ok = false;
        }

      encfs_test_check("write lowercase logical name", ok);
    }

  encfs_test_check("case-distinct name retains first content",
                  extended_contents(upper, (const uint8_t *)"UPPER",
                                    buffer, 5));
  encfs_test_check("case-distinct name retains second content",
                  extended_contents(lower, (const uint8_t *)"lower",
                                    buffer, 5));
  dir = opendir(EXT_ROOT);
  encfs_test_check("open case-sensitive directory", dir != NULL);
  if (dir != NULL)
    {
      while ((entry = readdir(dir)) != NULL)
        {
          upper_count += strcmp(entry->d_name, "CaseSensitive") == 0;
          lower_count += strcmp(entry->d_name, "casesensitive") == 0;
        }

      encfs_test_check("directory lists both case-distinct logical names",
                      upper_count == 1 && lower_count == 1);
      encfs_test_check("close case-sensitive directory", closedir(dir) == 0);
    }

  encfs_test_check("remove mixed-case sample", unlink(upper) == 0);
  encfs_test_check("remove lowercase sample", unlink(lower) == 0);
}

static void extended_paths(void)
{
  struct stat st;
  char *path;
  int fd;

  encfs_test_check("create normalization directory",
                  mkdir(EXT_ROOT "/sub", 0777) == 0);
  fd = open(EXT_ROOT "//sub/.././attributes", O_RDONLY);
  encfs_test_check("normalize repeated separators and dot components",
                  fd >= 0);
  if (fd >= 0)
    {
      path = malloc(PATH_MAX);
      encfs_test_check("allocate normalized FILEPATH buffer", path != NULL);
      if (path != NULL)
        {
          memset(path, 0, PATH_MAX);
          encfs_test_check("FILEPATH returns normalized logical path",
                          ioctl(fd, FIOC_FILEPATH,
                                (unsigned long)(uintptr_t)path) == 0 &&
                          strcmp(path, EXT_FILE) == 0);
          free(path);
        }

      encfs_test_check("close normalized sample", close(fd) == 0);
    }

  encfs_test_check("missing intermediate cannot be bypassed by dotdot",
                  stat(EXT_ROOT "/missing/../attributes", &st) < 0 &&
                  errno == ENOENT);
  encfs_test_check("file intermediate cannot be bypassed by dotdot",
                  stat(EXT_FILE "/../attributes", &st) < 0 &&
                  errno == ENOTDIR);
  encfs_test_check("dot after regular file requires directory",
                  stat(EXT_FILE "/.", &st) < 0 && errno == ENOTDIR);
  encfs_test_check("trailing slash after file requires directory",
                  stat(EXT_FILE "/", &st) < 0 && errno == ENOTDIR);

  /* FatFs maps FR_NO_PATH to ENOENT for a non-directory path component;
   * the upper path's explicit dot/dotdot checks above return ENOTDIR.
   */

  fd = open(EXT_FILE "/child", O_CREAT | O_RDWR, 0666);
  encfs_test_check("regular-file parent rejects child creation",
                  fd < 0 && (errno == ENOENT || errno == ENOTDIR));
  if (fd >= 0)
    {
      close(fd);
      unlink(EXT_FILE "/child");
    }

  encfs_test_check("normalize parent traversal within mounted volume",
                  stat(EXT_ROOT "/sub/../../acceptance-extended/attributes",
                       &st) == 0 && S_ISREG(st.st_mode));
  encfs_test_check("remove normalization directory",
                  rmdir(EXT_ROOT "/sub") == 0);
}

static size_t extended_encoded_length(size_t length)
{
  return 3 + ((length + 16) * 8 + 4) / 5;
}

static void extended_expansion(void)
{
  char *path;
  size_t logical;
  size_t physical;
  size_t saved;
  size_t encoded = extended_encoded_length(ENCPATHFS_NAME_MAX);
  int levels = 0;
  int ret;
  bool ok = true;

  path = malloc(PATH_MAX + ENCPATHFS_NAME_MAX + 2);
  encfs_test_check("allocate path-expansion buffer", path != NULL);
  if (path == NULL)
    {
      return;
    }

  strcpy(path, EXP_ROOT);
  ret = mkdir(path, 0777);
  encfs_test_check("create expansion boundary root", ret == 0);
  if (ret < 0)
    {
      free(path);
      return;
    }
  logical = strlen(path);
  physical = 2 + 1 + extended_encoded_length(strlen("exb"));
  while (physical + 1 + encoded < PATH_MAX)
    {
      path[logical++] = '/';
      memset(path + logical, 'x', ENCPATHFS_NAME_MAX);
      logical += ENCPATHFS_NAME_MAX;
      path[logical] = '\0';
      if (mkdir(path, 0777) < 0)
        {
          ok = false;
          path[logical - ENCPATHFS_NAME_MAX - 1] = '\0';
          logical -= ENCPATHFS_NAME_MAX + 1;
          break;
        }

      levels++;
      physical += encoded + 1;
    }

  encfs_test_check("nested 128-byte names fit until encoded path limit", ok);
  if (ok)
    {
      saved = logical;
      path[logical++] = '/';
      memset(path + logical, 'y', ENCPATHFS_NAME_MAX);
      logical += ENCPATHFS_NAME_MAX;
      path[logical] = '\0';
      ret = mkdir(path, 0777);
      encfs_test_check("encoded path expansion rejects before lower limit",
                      ret < 0 && errno == ENAMETOOLONG &&
                      strlen(path) < PATH_MAX);
      if (ret == 0)
        {
          rmdir(path);
        }

      path[saved] = '\0';
      logical = saved;
    }

  ok = true;
  while (levels-- > 0)
    {
      if (rmdir(path) < 0)
        {
          ok = false;
        }

      logical -= ENCPATHFS_NAME_MAX + 1;
      path[logical] = '\0';
    }

  encfs_test_check("remove nested expansion directories", ok);
  encfs_test_check("remove expansion boundary root", rmdir(EXP_ROOT) == 0);
  free(path);
}

static void extended_stress(void)
{
  static const size_t lengths[] =
    {1, 511, 512, 513, 1023, 4095, 4096, 4097, STRESS_MAX};
  static const char source[] = EXT_ROOT "/cycle-source";
  static const char renamed[] = EXT_ROOT "/cycle-renamed";
  struct timespec start;
  struct timespec finish;
  struct stat st;
  uint8_t *expected;
  uint8_t *readback;
  uint64_t bytes = 0;
  uint64_t elapsed = 0;
  unsigned int completed = 0;
  size_t length;
  size_t offset;
  size_t patch;
  size_t n;
  int saved_errno = 0;
  int fd = -1;
  bool clock_ok;
  bool ok;

  expected = malloc(STRESS_MAX);
  readback = malloc(STRESS_MAX);
  encfs_test_check("allocate multi-sector regression buffers",
                  expected != NULL && readback != NULL);
  clock_ok = clock_gettime(CLOCK_MONOTONIC, &start) == 0;
  if (expected != NULL && readback != NULL)
    {
      for (completed = 0; completed < STRESS_REQUESTED; completed++)
        {
          length = lengths[completed % (sizeof(lengths) / sizeof(lengths[0]))];
          for (n = 0; n < length; n++)
            {
              expected[n] = (uint8_t)(completed * 31 + n * 17 + (n >> 8));
            }

          fd = open(source, O_CREAT | O_EXCL | O_RDWR, 0666);
          if (fd < 0 || !extended_write(fd, expected, length))
            {
              break;
            }

          /* Change an offset for every cycle, including unaligned positions
           * around 512-byte sectors, then compare every byte and exact EOF.
           */

          offset = ((size_t)completed * 509 + 1) % length;
          patch = length - offset;
          if (patch > 73)
            {
              patch = 73;
            }

          for (n = 0; n < patch; n++)
            {
              expected[offset + n] ^= 0xa5;
            }

          if (lseek(fd, offset, SEEK_SET) != offset ||
              !extended_write(fd, expected + offset, patch) ||
              fsync(fd) < 0 || fstat(fd, &st) < 0 ||
              st.st_size != length || lseek(fd, 0, SEEK_SET) != 0 ||
              !extended_read(fd, readback, length) ||
              memcmp(expected, readback, length) != 0)
            {
              break;
            }

          ok = close(fd) == 0;
          fd = -1;
          if (!ok ||
              !extended_contents(source, expected, readback, length) ||
              rename(source, renamed) < 0 ||
              !extended_contents(renamed, expected, readback, length) ||
              unlink(renamed) < 0)
            {
              break;
            }

          bytes += length + patch;
        }

      saved_errno = errno;
    }

  if (clock_gettime(CLOCK_MONOTONIC, &finish) == 0 && clock_ok)
    {
      elapsed = ((uint64_t)finish.tv_sec - start.tv_sec) * 1000000000 +
                finish.tv_nsec - start.tv_nsec;
    }
  else
    {
      clock_ok = false;
    }

  if (fd >= 0)
    {
      close(fd);
    }

  unlink(source);
  unlink(renamed);
  free(expected);
  free(readback);
  printf("ENCFS_STRESS_RESULT requested=%u completed=%u bytes=%" PRIu64
         " elapsed_ns=%" PRIu64 "\n", (unsigned int)STRESS_REQUESTED, completed,
         bytes, elapsed);
  errno = saved_errno;
  encfs_test_check("1000-cycle create/write/fsync/read/rename/delete",
                  completed == STRESS_REQUESTED);
  encfs_test_check("stress monotonic timing available", clock_ok);
}

void encfs_test_extended(void)
{
  int ret;

  ret = mkdir(EXT_ROOT, 0777);
  encfs_test_check("create extended acceptance directory", ret == 0);
  if (ret < 0)
    {
      return;
    }

  extended_attributes();
  extended_case_names();
  extended_paths();
  extended_expansion();
  extended_stress();
  encfs_test_check("remove attribute sample", unlink(EXT_FILE) == 0);
  encfs_test_check("remove extended acceptance directory",
                  rmdir(EXT_ROOT) == 0);
}
