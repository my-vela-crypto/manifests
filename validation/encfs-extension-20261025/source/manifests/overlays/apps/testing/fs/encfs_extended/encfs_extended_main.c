/****************************************************************************
 * apps/testing/fs/encfs_extended/encfs_extended_main.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>
#include <sys/stat.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define ROOT "/data/power-fixture"
#define FILESIZE 65537

static unsigned char byte_at(unsigned generation, size_t offset)
{
  return (unsigned char)((offset * 29u + (offset >> 8) * 17u +
                          generation * 53u + 0xa7u) & 255u);
}

static int write_file(const char *path, size_t length, unsigned generation,
                      int slow)
{
  unsigned char block[1024];
  size_t offset = 0;
  int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
  if (fd < 0)
    {
      return -1;
    }

  while (offset < length)
    {
      size_t count = length - offset;
      size_t done = 0;
      size_t i;
      if (count > sizeof(block))
        {
          count = sizeof(block);
        }

      for (i = 0; i < count; i++)
        {
          block[i] = byte_at(generation, offset + i);
        }

      while (done < count)
        {
          ssize_t written = write(fd, block + done, count - done);
          if (written <= 0)
            {
              close(fd);
              return -1;
            }

          done += written;
        }

      offset += count;
      if (slow && offset == 4096)
        {
          printf("ENCFS_POWER_INFLIGHT generation=%u offset=%zu\n",
                 generation, offset);
          fflush(stdout);
        }

      if (slow)
        {
          usleep(10000);
        }
    }

  if (fsync(fd) < 0)
    {
      close(fd);
      return -1;
    }

  return close(fd);
}

static int verify_file(const char *path, size_t length, unsigned generation)
{
  unsigned char block[1024];
  struct stat st;
  size_t offset = 0;
  int fd = open(path, O_RDONLY);
  if (fd < 0)
    {
      return -1;
    }

  if (fstat(fd, &st) < 0 || st.st_size != length)
    {
      close(fd);
      errno = EILSEQ;
      return -1;
    }

  while (offset < length)
    {
      ssize_t count = read(fd, block, sizeof(block));
      ssize_t i;
      if (count <= 0)
        {
          close(fd);
          errno = EILSEQ;
          return -1;
        }

      for (i = 0; i < count; i++)
        {
          if (block[i] != byte_at(generation, offset + i))
            {
              close(fd);
              errno = EILSEQ;
              return -1;
            }
        }

      offset += count;
    }

  if (read(fd, block, 1) != 0)
    {
      close(fd);
      errno = EILSEQ;
      return -1;
    }

  return close(fd);
}

static int prepare(void)
{
  static const size_t lengths[] = {1, 513, FILESIZE};
  unsigned i;
  char path[96];
  if (mkdir(ROOT, 0700) < 0 && errno != EEXIST)
    {
      return -1;
    }

  for (i = 0; i < 3; i++)
    {
      snprintf(path, sizeof(path), ROOT "/sentinel%u", i);
      if (write_file(path, lengths[i], i + 1, 0) < 0)
        {
          return -1;
        }
    }

  if (write_file(ROOT "/committed-0", FILESIZE, 0, 0) < 0)
    {
      return -1;
    }

  sync();
  printf("ENCFS_POWER_PREPARED sentinels=3 committed_generation=0\n");
  return 0;
}

static int verify(unsigned expected)
{
  static const size_t lengths[] = {1, 513, FILESIZE};
  unsigned i;
  char path[96];
  for (i = 0; i < 3; i++)
    {
      snprintf(path, sizeof(path), ROOT "/sentinel%u", i);
      if (verify_file(path, lengths[i], i + 1) < 0)
        {
          return -1;
        }
    }

  for (i = 0; i <= expected; i++)
    {
      snprintf(path, sizeof(path), ROOT "/committed-%u", i);
      if (verify_file(path, FILESIZE, i) < 0)
        {
          return -1;
        }
    }

  printf("ENCFS_POWER_VERIFIED sentinels=3 committed_generation=%u bytes=%u\n",
         expected, 1 + 513 + FILESIZE * (expected + 2));
  return 0;
}

int main(int argc, char *argv[])
{
  int ret = -1;
  unsigned generation = argc > 2 ? strtoul(argv[2], NULL, 10) : 0;
  if (argc == 2 && strcmp(argv[1], "clock") == 0)
    {
      struct stat st;
      time_t now;
      ret = write_file("/data/clock-probe", 513, 0, 0);
      if (ret == 0)
        {
          ret = stat("/data/clock-probe", &st);
        }

      if (ret == 0)
        {
          now = time(NULL);
          printf("ENCFS_TIME_RESULT now=%lld mtime=%lld delta=%lld\n",
                 (long long)now, (long long)st.st_mtime,
                 (long long)st.st_mtime - (long long)now);
          ret = unlink("/data/clock-probe");
        }
    }
  else if (argc == 2 && strcmp(argv[1], "prepare") == 0)
    {
      ret = prepare();
    }
  else if (argc == 3 && strcmp(argv[1], "verify") == 0)
    {
      ret = verify(generation);
    }
  else if (argc == 3 && strcmp(argv[1], "churn") == 0 && generation > 0)
    {
      char path[96];
      while (generation < 100000)
        {
          snprintf(path, sizeof(path), ROOT "/committed-%u", generation);
          if (write_file(ROOT "/inflight", FILESIZE, generation, 1) < 0 ||
              rename(ROOT "/inflight", path) < 0)
            {
              break;
            }

          sync();
          printf("ENCFS_POWER_COMMITTED generation=%u\n", generation);
          fflush(stdout);
          generation++;
        }
    }
  else
    {
      fprintf(stderr, "Usage: encfs_extended prepare|verify <generation>|"
                      "churn <first_generation>\n");
      return 2;
    }

  printf("ENCFS_EXTENDED_RESULT action=%s result=%d errno=%d\n",
         argv[1], ret, ret < 0 ? errno : 0);
  fflush(stdout);
  return ret < 0 ? 1 : 0;
}
