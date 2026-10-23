/****************************************************************************
 * apps/examples/encfs_test/encfs_test_main.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/crypto/aes_siv.h>
#include <nuttx/crypto/blkcrypt.h>
#include <nuttx/crypto/hkdf.h>
#include <nuttx/fs/encfs.h>
#include <nuttx/fs/encpathfs.h>
#include <nuttx/fs/fs.h>
#include <nuttx/fs/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "encfs_test_shared.h"

/* This long marker is searched in the actual host image after clean shutdown. */

#define TEST_CONTENT "ENCFS_COURSE_2026_SECRET_PAYLOAD_7d64f98ac0e34251_" \
                     "persistent-content-and-transparent-path-encryption"
#define TEST_FILE "/data/test/secret.txt"

static unsigned int g_pass;
static unsigned int g_fail;

#define CHECK(label, condition) do \
  { \
    if (condition) \
      { \
        g_pass++; \
        printf("[PASS] %s\n", label); \
      } \
    else \
      { \
        g_fail++; \
        printf("[FAIL] %s errno=%d\n", label, errno); \
      } \
  } while (0)

void encfs_test_check(const char *label, bool condition)
{
  CHECK(label, condition);
}

static size_t unhex(const char *hex, uint8_t *out)
{
  size_t n = 0;
  unsigned int byte;

  while (hex[0] && hex[1])
    {
      if (sscanf(hex, "%2x", &byte) != 1)
        {
          break;
        }

      out[n++] = byte;
      hex += 2;
    }

  return n;
}

static int read_matches(const char *path, const char *expected)
{
  char buffer[256];
  ssize_t n;
  int fd = open(path, O_RDONLY);

  if (fd < 0)
    {
      return 0;
    }

  n = read(fd, buffer, sizeof(buffer));
  close(fd);
  return n == strlen(expected) && memcmp(buffer, expected, n) == 0;
}

static void crypto_tests(void)
{
  uint8_t key[32];
  uint8_t ad[24];
  uint8_t plain[32];
  uint8_t sealed[64];
  uint8_t expected[64];
  uint8_t result[64];
  uint8_t ikm[22];
  uint8_t salt[13];
  uint8_t info[10];
  char encoded[ENCPATHFS_ENCODED_MAX + 2];
  char again[ENCPATHFS_ENCODED_MAX + 2];
  char decoded[ENCPATHFS_NAME_MAX + 2];
  char boundary[ENCPATHFS_NAME_MAX + 2];
  size_t i;

  memset(ikm, 0x0b, sizeof(ikm));
  for (i = 0; i < sizeof(salt); i++) salt[i] = i;
  for (i = 0; i < sizeof(info); i++) info[i] = 0xf0 + i;
  unhex("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c"
        "5db02d56ecc4c5bf34007208d5b887185865", expected);
  CHECK("RFC5869 test case 1",
        hkdf_sha256(ikm, sizeof(ikm), salt, sizeof(salt), info,
                    sizeof(info), result, 42) == 0 &&
        memcmp(result, expected, 42) == 0);

  unhex("fffefdfcfbfaf9f8f7f6f5f4f3f2f1f0"
        "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff", key);
  unhex("101112131415161718191a1b1c1d1e1f2021222324252627", ad);
  unhex("112233445566778899aabbccddee", plain);
  unhex("85632d07c6e8f37f950acd320a2ecc93"
        "40c02b9690c4dc04daef7f6afe5c", expected);
  CHECK("RFC5297 deterministic vector",
        aes_siv_encrypt(key, ad, sizeof(ad), plain, 14, sealed) == 0 &&
        memcmp(sealed, expected, 30) == 0);
  CHECK("AES-SIV decrypt",
        aes_siv_decrypt(key, ad, sizeof(ad), sealed, 30, result) == 0 &&
        memcmp(result, plain, 14) == 0);
  sealed[20] ^= 1;
  CHECK("AES-SIV rejects tampering",
        aes_siv_decrypt(key, ad, sizeof(ad), sealed, 30, result) < 0);

  CHECK("name encode", encpathfs_encode_name(key, "secret.txt", encoded,
                                             sizeof(encoded)) == 0);
  CHECK("name round trip", encpathfs_decode_name(key, encoded, decoded,
                                                sizeof(decoded)) == 0 &&
                           strcmp(decoded, "secret.txt") == 0);
  CHECK("deterministic name", encpathfs_encode_name(key, "secret.txt", again,
                                                    sizeof(again)) == 0 &&
                              strcmp(encoded, again) == 0);
  key[0] ^= 1;
  CHECK("name rejects wrong key", encpathfs_decode_name(key, encoded, decoded,
                                                      sizeof(decoded)) < 0);
  key[0] ^= 1;
  encoded[4] = encoded[4] == '0' ? '1' : '0';
  CHECK("name rejects tampering", encpathfs_decode_name(key, encoded, decoded,
                                                      sizeof(decoded)) < 0);
  CHECK("name rejects invalid Base32", encpathfs_decode_name(key, "E1_!bad",
                                                           decoded,
                                                           sizeof(decoded)) < 0);
  memset(boundary, 'a', ENCPATHFS_NAME_MAX);
  boundary[ENCPATHFS_NAME_MAX] = 0;
  CHECK("128-byte name", encpathfs_encode_name(key, boundary, encoded,
                                              sizeof(encoded)) == 0 &&
                         strlen(encoded) == ENCPATHFS_ENCODED_MAX);
  boundary[ENCPATHFS_NAME_MAX] = 'a';
  boundary[ENCPATHFS_NAME_MAX + 1] = 0;
  CHECK("129-byte name rejected", encpathfs_encode_name(key, boundary, encoded,
                                                       sizeof(encoded)) ==
                                   -ENAMETOOLONG);
  CHECK("structural component rejected", encpathfs_encode_name(key, "..",
                                                              encoded,
                                                              sizeof(encoded)) < 0);
}

static void filesystem_tests(void)
{
  char path[300];
  char buffer[32];
  struct stat st;
  struct statfs fs;
  struct dirent *entry;
  DIR *dir;
  int fd;
  int copy;
  int found;
  int i;

  CHECK("mkdir logical directory", mkdir("/data/test", 0777) == 0 ||
                                         errno == EEXIST);
  fd = open(TEST_FILE, O_CREAT | O_TRUNC | O_RDWR, 0666);
  CHECK("open logical file", fd >= 0);
  if (fd >= 0)
    {
      CHECK("write content", write(fd, TEST_CONTENT, strlen(TEST_CONTENT)) ==
                             strlen(TEST_CONTENT));
      CHECK("fsync file", fsync(fd) == 0);
      CHECK("fstat file", fstat(fd, &st) == 0 &&
                           st.st_size == strlen(TEST_CONTENT));
      CHECK("seek file", lseek(fd, 6, SEEK_SET) == 6);
      CHECK("random read", read(fd, buffer, 7) == 7 &&
                            memcmp(buffer, TEST_CONTENT + 6, 7) == 0);
      copy = dup(fd);
      CHECK("dup file", copy >= 0);
      if (copy >= 0)
        {
          CHECK("dup read", read(copy, buffer, 5) == 5 &&
                             memcmp(buffer, TEST_CONTENT + 13, 5) == 0);
          close(copy);
        }

      CHECK("close file", close(fd) == 0);
    }

  CHECK("read content", read_matches(TEST_FILE, TEST_CONTENT));
  CHECK("stat logical path", stat(TEST_FILE, &st) == 0);
  CHECK("statfs logical limit", statfs("/data", &fs) == 0 &&
                                 fs.f_namelen == ENCPATHFS_NAME_MAX);
  CHECK("missing parent rejected", open("/data/missing/file", O_CREAT | O_RDWR,
                                        0666) < 0 && errno == ENOENT);
  CHECK("mkdir move source", mkdir("/data/move", 0777) == 0);
  CHECK("unlink rejects directory", unlink("/data/move") < 0 &&
                                    errno == EISDIR);
  CHECK("mkdir with trailing slash", mkdir("/data/trail/", 0777) == 0);
  CHECK("remove trailing-slash sample", rmdir("/data/trail") == 0);
  fd = open("/data/move/child", O_CREAT | O_RDWR, 0666);
  CHECK("create moved child", fd >= 0);
  if (fd >= 0) close(fd);
  CHECK("rename whole directory", rename("/data/move", "/data/moved") == 0);
  CHECK("child survives directory rename", stat("/data/moved/child", &st) == 0);
  CHECK("cross-directory rename", rename("/data/moved/child",
                                          "/data/test/moved.txt") == 0);
  CHECK("existing target rename rejected", rename("/data/test/moved.txt",
                                                  TEST_FILE) < 0 &&
                                                errno == EEXIST);
  CHECK("unlink moved file", unlink("/data/test/moved.txt") == 0);
  CHECK("rmdir moved directory", rmdir("/data/moved") == 0);

  fd = open("/data/resize", O_CREAT | O_TRUNC | O_RDWR, 0666);
  CHECK("open truncate sample", fd >= 0);
  if (fd >= 0)
    {
      CHECK("write truncate sample", write(fd, "abcdefghij", 10) == 10);
      CHECK("truncate file", ftruncate(fd, 4) == 0 &&
                             fstat(fd, &st) == 0 && st.st_size == 4);
      close(fd);
      CHECK("unlink truncate sample", unlink("/data/resize") == 0);
    }

  memset(path, 0, sizeof(path));
  strcpy(path, "/data/");
  memset(path + 6, 'n', ENCPATHFS_NAME_MAX);
  fd = open(path, O_CREAT | O_TRUNC | O_RDWR, 0666);
  CHECK("create longest logical name", fd >= 0);
  if (fd >= 0)
    {
      close(fd);
      CHECK("unlink longest logical name", unlink(path) == 0);
    }

  path[6 + ENCPATHFS_NAME_MAX] = 'n';
  CHECK("overlong logical name rejected", open(path, O_CREAT | O_RDWR, 0666) < 0
                                         && errno == ENAMETOOLONG);
  dir = opendir("/data/test");
  CHECK("opendir", dir != NULL);
  if (dir)
    {
      found = 0;
      while ((entry = readdir(dir)) != NULL)
        if (strcmp(entry->d_name, "secret.txt") == 0) found++;
      CHECK("readdir restores logical names", found == 1);
      rewinddir(dir);
      found = 0;
      while ((entry = readdir(dir)) != NULL)
        if (strcmp(entry->d_name, "secret.txt") == 0) found++;
      CHECK("rewinddir", found == 1);
      closedir(dir);
    }

  for (i = 0; i < 24; i++)
    {
      snprintf(path, sizeof(path), "/data/stress-%02d", i);
      fd = open(path, O_CREAT | O_TRUNC | O_RDWR, 0666);
      if (fd < 0) break;
      if (write(fd, TEST_CONTENT, strlen(TEST_CONTENT)) != strlen(TEST_CONTENT))
        {
          close(fd);
          break;
        }

      close(fd);
      if (!read_matches(path, TEST_CONTENT) || unlink(path) < 0) break;
    }

  CHECK("24-cycle file regression", i == 24);
  encfs_test_extended();
}

static void lifecycle_tests(void)
{
  struct encfs_status_s status;
  struct inode *block;
  struct geometry geometry;
  uint8_t *sector;
  uint8_t wrong[32];
  DIR *dir;
  int fd;
  int ret;
  int i;

  memset(wrong, 0xa6, sizeof(wrong));
  CHECK("start is idempotent", encfs_start() == 0);
  CHECK("mounted block rejects direct key replacement",
        blkcrypt_set_key(ENCFS_BLOCK_DEVICE, wrong, sizeof(wrong)) == -EBUSY);
  CHECK("mounted block rejects unregister",
        blkcrypt_unregister(ENCFS_BLOCK_DEVICE) == -EBUSY);
  CHECK("busy unregister preserves node", access(ENCFS_BLOCK_DEVICE, F_OK) == 0);
  ret = open_blockdriver(ENCFS_BLOCK_DEVICE, 0, &block);
  CHECK("open mounted block for ioctl", ret == 0);
  if (ret == 0)
    {
      CHECK("mounted block rejects DIOC_SETKEY",
            block->u.i_bops->ioctl(block, DIOC_SETKEY,
                                  (unsigned long)(uintptr_t)wrong) == -EBUSY);
      CHECK("close mounted block probe", close_blockdriver(block) == 0);
    }

  CHECK("mounted block rejects unlink", unlink(ENCFS_BLOCK_DEVICE) < 0 &&
                                          errno == EBUSY);
  CHECK("busy unlink preserves content", read_matches(TEST_FILE, TEST_CONTENT));
  fd = open(TEST_FILE, O_RDONLY);
  CHECK("open busy file", fd >= 0);
  if (fd >= 0)
    {
      CHECK("open file prevents stop", encfs_stop() == -EBUSY);
      CHECK("busy stop keeps READY", encfs_get_status(&status) == 0 &&
                                      status.state == ENCFS_READY);
      CHECK("busy mount rejects key replacement",
            encfs_test_set_master(wrong, sizeof(wrong)) == -EBUSY);
      close(fd);
    }

  dir = opendir("/data");
  CHECK("open busy directory", dir != NULL);
  if (dir)
    {
      CHECK("open directory prevents stop", encfs_stop() == -EBUSY);
      closedir(dir);
    }

  for (i = 0; i < 100; i++)
    {
      if (encfs_stop() < 0 || encfs_start() < 0 ||
          !read_matches(TEST_FILE, TEST_CONTENT)) break;
    }

  printf("ENCFS_RESTART_RESULT requested=100 completed=%d\n", i);
  CHECK("100 restart cycles", i == 100);
  CHECK("stop before negative tests", encfs_stop() == 0);
  CHECK("stopped is idempotent", encfs_stop() == 0);
  ret = blkcrypt_register(ENCFS_BLOCK_DEVICE, ENCFS_PARENT_DEVICE);
  CHECK("register block without a key", ret == 0);
  if (ret == 0)
    {
      ret = open_blockdriver(ENCFS_BLOCK_DEVICE, 0, &block);
      CHECK("open unkeyed block", ret == 0);
      if (ret == 0)
        {
          ret = block->u.i_bops->geometry(block, &geometry);
          sector = ret == 0 ? calloc(1, geometry.geo_sectorsize) : NULL;
          CHECK("query unkeyed block geometry", sector != NULL);
          if (sector != NULL)
            {
              CHECK("unkeyed block rejects reads",
                    block->u.i_bops->read(block, sector, 0, 1) == -EACCES);
              CHECK("unkeyed block rejects writes",
                    block->u.i_bops->write(block, sector, 0, 1) == -EACCES);
              free(sector);
            }

          CHECK("close unkeyed block", close_blockdriver(block) == 0);
        }

      CHECK("remove unkeyed block", blkcrypt_unregister(ENCFS_BLOCK_DEVICE) == 0);
    }

  CHECK("select absent master", encfs_test_set_master(NULL, 0) == 0);
  CHECK("absent master rejects start", encfs_start() < 0);
  CHECK("absent master leaves no block node", access(ENCFS_BLOCK_DEVICE, F_OK) < 0);
  memset(wrong, 0xa6, sizeof(wrong));
  CHECK("select wrong master", encfs_test_set_master(wrong, sizeof(wrong)) == 0);
  CHECK("wrong master rejects start", encfs_start() < 0);
  CHECK("wrong master leaves no block node", access(ENCFS_BLOCK_DEVICE, F_OK) < 0);
  CHECK("restore board master", encfs_test_use_board_key() == 0);
  for (i = ENCFS_TEST_FAIL_REGISTER; i <= ENCFS_TEST_FAIL_UPPER; i++)
    {
      CHECK("set startup failpoint", encfs_test_set_failpoint(i) == 0);
      ret = encfs_start();
      CHECK("injected failure propagates", ret == -EIO);
      CHECK("injected failure rolls back", encfs_get_status(&status) == 0 &&
              status.state == ENCFS_STOPPED && !status.block_registered &&
              !status.upper_mounted && !status.lower_visible &&
              !status.path_key_staged);
      CHECK("retry after failure", encfs_start() == 0 &&
                                   read_matches(TEST_FILE, TEST_CONTENT));
      CHECK("stop after retry", encfs_stop() == 0);
    }

  CHECK("restart after negative tests", encfs_start() == 0);
  CHECK("negative tests preserve content", read_matches(TEST_FILE, TEST_CONTENT));
}

static void external_unmount_tests(void)
{
  struct encfs_status_s status;
  int ret;
  int fd;

  ret = umount(ENCFS_UPPER_PATH);
  CHECK("external upper unmount", ret == 0);
  if (ret < 0)
    {
      return;
    }

  CHECK("status reconciles externally removed upper",
        encfs_get_status(&status) == 0 && !status.upper_mounted &&
        !status.lower_visible && status.state != ENCFS_READY);
  ret = encfs_start();
  CHECK("start never falsely reports externally removed upper ready",
        (ret == 0 && read_matches(TEST_FILE, TEST_CONTENT)) || ret == -EBUSY);
  CHECK("stop recovers after external upper unmount", encfs_stop() == 0);
  CHECK("external unmount recovery releases block node",
        access(ENCFS_BLOCK_DEVICE, F_OK) < 0);
  CHECK("restart after external upper unmount", encfs_start() == 0 &&
        read_matches(TEST_FILE, TEST_CONTENT));

  ret = umount(ENCFS_UPPER_PATH);
  CHECK("external unmount before unrelated target", ret == 0);
  if (ret < 0)
    {
      return;
    }

  ret = mount(NULL, ENCFS_UPPER_PATH, "tmpfs", 0, NULL);
  CHECK("mount unrelated filesystem at former upper", ret == 0);
  if (ret == 0)
    {
      fd = open("/data/unrelated-marker", O_CREAT | O_TRUNC | O_WRONLY, 0666);
      CHECK("create unrelated mount marker", fd >= 0);
      if (fd >= 0)
        {
          CHECK("write unrelated mount marker", write(fd, "foreign", 7) == 7);
          close(fd);
        }

      ret = encfs_stop();
      CHECK("stop handles replaced target without unrelated unmount",
            (ret == 0 || ret == -EBUSY) &&
            read_matches("/data/unrelated-marker", "foreign"));
      CHECK("start refuses unrelated upper mount", encfs_start() < 0 &&
            read_matches("/data/unrelated-marker", "foreign"));
      CHECK("remove unrelated filesystem", umount(ENCFS_UPPER_PATH) == 0);
    }

  CHECK("cleanup after unrelated upper mount", encfs_stop() == 0);
  CHECK("restart after unrelated upper mount", encfs_start() == 0 &&
        read_matches(TEST_FILE, TEST_CONTENT));
}

static void standalone_tests(void)
{
  uint8_t key[32];
  int ret;
  size_t i;
  struct stat st;

  CHECK("stop encrypted chain", encfs_stop() == 0);
  for (i = 0; i < sizeof(key); i++) key[i] = i + 1;
  ret = mount(ENCFS_PARENT_DEVICE, ENCFS_LOWER_PATH, "fatfs", 0, "forceformat");
  CHECK("format standalone plaintext FAT", ret == 0);
  if (ret < 0) return;
  ret = encpathfs_initialize(ENCFS_LOWER_PATH, key, sizeof(key));
  CHECK("initialize standalone path volume", ret == 0);
  CHECK("initialization does not overwrite an existing volume",
        encpathfs_initialize(ENCFS_LOWER_PATH, key, sizeof(key)) == -EEXIST);
  key[0] ^= 1;
  CHECK("stage wrong path key", encpathfs_setkey(0, key, sizeof(key)) == 0);
  CHECK("path metadata rejects wrong key",
        mount(NULL, ENCFS_UPPER_PATH, "encpathfs", 0,
              "lower=/datap,root=.p,slot=0") < 0);
  CHECK("wrong path key leaves lower available", stat("/datap/.p", &st) == 0);
  key[0] ^= 1;
  CHECK("set standalone key", encpathfs_setkey(0, key, sizeof(key)) == 0);
  ret = mount(NULL, ENCFS_UPPER_PATH, "encpathfs", 0,
              "lower=/datap,root=.p,slot=0");
  CHECK("mount standalone path layer", ret == 0);
  if (ret < 0)
    {
      umount(ENCFS_LOWER_PATH);
      return;
    }

  CHECK("public lower path hidden", stat("/datap/.p", &st) < 0);
  filesystem_tests();
  CHECK("unmount standalone path layer", umount(ENCFS_UPPER_PATH) == 0);
}

int main(int argc, FAR char *argv[])
{
  const char *mode = argc == 2 ? argv[1] : "all";

  g_pass = 0;
  g_fail = 0;
  if (strcmp(mode, "crypto") == 0)
    {
      crypto_tests();
    }
  else if (strcmp(mode, "standalone") == 0)
    {
      crypto_tests();
      standalone_tests();
    }
  else if (strcmp(mode, "corruption") == 0)
    {
      encfs_test_corruption();
    }
  else if (strcmp(mode, "write") == 0 || strcmp(mode, "all") == 0)
    {
      crypto_tests();
      CHECK("start encrypted chain", encfs_start() == 0);
      filesystem_tests();
      lifecycle_tests();
      external_unmount_tests();
      CHECK("clean stop", encfs_stop() == 0);
    }
  else if (strcmp(mode, "verify") == 0)
    {
      CHECK("restart existing encrypted volume", encfs_start() == 0);
      CHECK("persistent content after reboot", read_matches(TEST_FILE, TEST_CONTENT));
      CHECK("persistent logical directory", access("/data/test", F_OK) == 0);
      CHECK("clean stop after reboot", encfs_stop() == 0);
    }
  else
    {
      fprintf(stderr, "Usage: encfs_test "
                      "crypto|standalone|corruption|write|verify|all\n");
      return EXIT_FAILURE;
    }

  printf("ENCFS_TEST_RESULT mode=%s pass=%u fail=%u\n", mode, g_pass, g_fail);
  return g_fail ? EXIT_FAILURE : EXIT_SUCCESS;
}
