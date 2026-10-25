/****************************************************************************
 * apps/examples/encfs_test/encfs_test_rekey.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <nuttx/fs/encfs.h>

#include "encfs_test_shared.h"

#if defined(CONFIG_ENCFS_KEYWRAP) && defined(CONFIG_ENCFS_TESTING)

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define REKEY_NAME "online-rekey-verification.bin"
#define REKEY_FILE "/data/" REKEY_NAME
#define REKEY_LENGTH 8193

#define REKEY_CHECK(label, condition) do \
  { \
    bool success = (condition); \
    encfs_test_check(label, success); \
    if (success) pass++; else fail++; \
  } while (0)

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint8_t rekey_byte(size_t offset)
{
  return (uint8_t)((offset * 37 + (offset >> 8) * 13 + 0x61) & 0xff);
}

static int rekey_write(int fd, size_t begin, size_t length)
{
  uint8_t buffer[513];
  size_t i;
  size_t count;
  size_t done;
  ssize_t result;

  while (length > 0)
    {
      count = length < sizeof(buffer) ? length : sizeof(buffer);
      for (i = 0; i < count; i++)
        {
          buffer[i] = rekey_byte(begin + i);
        }

      done = 0;
      while (done < count)
        {
          result = write(fd, buffer + done, count - done);
          if (result <= 0)
            {
              return -1;
            }

          done += result;
        }

      begin += count;
      length -= count;
    }

  return fsync(fd);
}

static bool rekey_read(int fd, size_t length)
{
  uint8_t buffer[511];
  size_t offset = 0;
  size_t i;
  ssize_t count;

  if (lseek(fd, 0, SEEK_SET) != 0)
    {
      return false;
    }

  while (offset < length)
    {
      count = read(fd, buffer, sizeof(buffer));
      if (count <= 0 || count > length - offset)
        {
          return false;
        }

      for (i = 0; i < count; i++)
        {
          if (buffer[i] != rekey_byte(offset + i))
            {
              return false;
            }
        }

      offset += count;
    }

  return read(fd, buffer, sizeof(buffer)) == 0;
}

static bool rekey_directory(FAR DIR *directory)
{
  FAR struct dirent *entry;
  bool found = false;

  if (directory == NULL)
    {
      return false;
    }

  errno = 0;
  while ((entry = readdir(directory)) != NULL)
    {
      if (strcmp(entry->d_name, REKEY_NAME) == 0)
        {
          found = true;
        }
    }

  return found && errno == 0;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int encfs_test_rekey(unsigned int next_master_id)
{
  struct encfs_status_s before;
  struct encfs_status_s after;
  struct stat statbuf;
  DIR *directory = NULL;
  bool created = false;
  unsigned int pass = 0;
  unsigned int fail = 0;
  int fd = -1;
  int copy = -1;
  int ret;

  REKEY_CHECK("online rekey requires READY wrapped volume",
              encfs_get_status(&before) == 0 &&
              before.state == ENCFS_READY && before.key_wrapped &&
              before.master_id != next_master_id);
  if (fail != 0)
    {
      goto out;
    }

  fd = open(REKEY_FILE, O_CREAT | O_EXCL | O_RDWR, 0600);
  REKEY_CHECK("create exclusive live rekey file", fd >= 0);
  if (fd < 0)
    {
      goto out;
    }

  created = true;
  REKEY_CHECK("write live file before rekey", rekey_write(fd, 0, 4097) == 0);
  copy = dup(fd);
  directory = opendir("/data");
  REKEY_CHECK("hold duplicate and directory across rekey",
              copy >= 0 && directory != NULL);
  REKEY_CHECK("open handles prevent unmount", encfs_stop() == -EBUSY);
  REKEY_CHECK("same master id does not claim rotation",
              encfs_rekey(before.master_id) == -EALREADY);
  REKEY_CHECK("install one-shot slot commit failure",
              encfs_test_keyslot_fail_commit(true) == 0);
  REKEY_CHECK("failed slot commit is reported",
              encfs_rekey(next_master_id) == -EIO);
  REKEY_CHECK("failed rekey preserves active wrapping version",
              encfs_get_status(&after) == 0 &&
              after.state == ENCFS_READY && after.key_wrapped &&
              after.master_id == before.master_id &&
              after.key_generation == before.key_generation);
  REKEY_CHECK("same open descriptor survives failed rekey",
              rekey_read(fd, 4097));

  ret = encfs_rekey(next_master_id);
  REKEY_CHECK("rotate master while file and DIR remain open", ret == 0);
  if (ret < 0)
    {
      goto out;
    }

  REKEY_CHECK("new wrapper version is active without unmount",
              encfs_get_status(&after) == 0 &&
              after.state == ENCFS_READY && after.key_wrapped &&
              after.master_id == next_master_id &&
              after.key_generation == before.key_generation + 1);
  REKEY_CHECK("same descriptor reads unchanged data after rekey",
              rekey_read(fd, 4097));
  REKEY_CHECK("same open DIR returns logical names after rekey",
              rekey_directory(directory));
  if (directory != NULL)
    {
      rewinddir(directory);
      REKEY_CHECK("same DIR rewinds and reads after rekey",
                  rekey_directory(directory));
    }

  REKEY_CHECK("append through duplicate after rekey",
              copy >= 0 && lseek(copy, 0, SEEK_END) == 4097 &&
              rekey_write(copy, 4097, REKEY_LENGTH - 4097) == 0);
  REKEY_CHECK("original descriptor sees post-rekey writes",
              rekey_read(fd, REKEY_LENGTH));
  REKEY_CHECK("post-rekey logical size remains correct",
              fstat(fd, &statbuf) == 0 && statbuf.st_size == REKEY_LENGTH);

  if (copy >= 0)
    {
      REKEY_CHECK("close post-rekey duplicate", close(copy) == 0);
      copy = -1;
    }

  if (directory != NULL)
    {
      REKEY_CHECK("close post-rekey directory", closedir(directory) == 0);
      directory = NULL;
    }

  REKEY_CHECK("close live rekey descriptor", close(fd) == 0);
  fd = -1;
  REKEY_CHECK("stop newly wrapped volume", encfs_stop() == 0);
  REKEY_CHECK("restart using new provider master", encfs_start() == 0);
  fd = open(REKEY_FILE, O_RDONLY);
  REKEY_CHECK("reopen file using new wrapper", fd >= 0);
  REKEY_CHECK("read all data after wrapper restart",
              fd >= 0 && rekey_read(fd, REKEY_LENGTH));

out:
  encfs_test_keyslot_fail_commit(false);
  if (copy >= 0)
    {
      REKEY_CHECK("cleanup duplicated rekey descriptor", close(copy) == 0);
    }

  if (directory != NULL)
    {
      REKEY_CHECK("cleanup rekey directory", closedir(directory) == 0);
    }

  if (fd >= 0)
    {
      REKEY_CHECK("cleanup rekey file descriptor", close(fd) == 0);
    }

  if (created)
    {
      REKEY_CHECK("remove owned rekey test file", unlink(REKEY_FILE) == 0);
    }

  printf("ENCFS_REKEY_RESULT pass=%u fail=%u next_master_id=%u\n",
         pass, fail, next_master_id);
  return fail == 0 ? 0 : -1;
}

#endif
