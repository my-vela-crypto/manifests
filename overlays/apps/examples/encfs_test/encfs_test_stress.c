/****************************************************************************
 * apps/examples/encfs_test/encfs_test_stress.c
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/fs/encfs.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <malloc.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "encfs_test_shared.h"

#define STRESS_WORKERS       2
#define STRESS_MAX_FILE      65537
#define STRESS_PROGRESS_MS   30000
#define STRESS_SEED          UINT32_C(0x6e637273)
#define STRESS_ROOT          "/data/.encfs-stress"

/* Each worker owns one live file and two directories.  It removes the file
 * after every transaction, so duration cannot exhaust the data volume.
 * O_EXCL and mkdir without EEXIST fallback avoid replacing unrelated files.
 * No formatting, key replacement, mount changes, or arbitrary tree removal
 * are performed here.  The two workers deliberately use the same mount and
 * block device concurrently; the GUI may continue using /data throughout.
 */

struct stress_context_s
{
  pthread_mutex_t lock;
  uint64_t start_ms;
  uint64_t deadline_ms;
  uint64_t iterations;
  uint64_t written;
  uint64_t verified;
  uint64_t worker_iterations[STRESS_WORKERS];
  uint64_t worker_elapsed_ms[STRESS_WORKERS];
  unsigned int errors;
  bool stop;
  const char *error_stage;
  int error_code;
  unsigned int error_worker;
  char root[80];
};

struct stress_worker_s
{
  struct stress_context_s *context;
  unsigned int number;
  char incoming[112];
  char committed[112];
};

static uint64_t stress_now_ms(void)
{
  struct timespec now;

  if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
    {
      return UINT64_MAX;
    }

  return (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void stress_error(struct stress_worker_s *worker,
                         const char *stage, int error)
{
  struct stress_context_s *context = worker->context;

  pthread_mutex_lock(&context->lock);
  context->errors++;
  context->stop = true;
  if (context->error_stage == NULL)
    {
      context->error_stage = stage;
      context->error_code = error != 0 ? error : EIO;
      context->error_worker = worker->number;
    }

  pthread_mutex_unlock(&context->lock);
}

/* A fixed, documented seed and a stateless byte oracle make every file's
 * expected bytes reproducible independently of read/write chunk boundaries.
 */

static uint8_t stress_byte(uint32_t seed, size_t offset)
{
  uint32_t word = seed ^ ((uint32_t)offset * UINT32_C(0x9e3779b9));

  word ^= word >> 16;
  word *= UINT32_C(0x7feb352d);
  word ^= word >> 15;
  word *= UINT32_C(0x846ca68b);
  word ^= word >> 16;
  return (uint8_t)word;
}

static bool stress_write(int fd, const uint8_t *buffer, size_t length,
                         size_t chunk)
{
  size_t done = 0;
  size_t requested;
  ssize_t result;

  while (done < length)
    {
      requested = length - done < chunk ? length - done : chunk;
      result = write(fd, buffer + done, requested);
      if (result < 0 && errno == EINTR)
        {
          continue;
        }

      if (result <= 0)
        {
          if (result == 0)
            {
              errno = EIO;
            }

          return false;
        }

      done += result;
    }

  return true;
}

static bool stress_verify(int fd, const uint8_t *expected,
                          uint8_t *actual, size_t length)
{
  size_t done = 0;
  size_t requested;
  ssize_t result;
  uint8_t extra;

  while (done < length)
    {
      requested = length - done < 997 ? length - done : 997;
      result = read(fd, actual + done, requested);
      if (result < 0 && errno == EINTR)
        {
          continue;
        }

      if (result <= 0)
        {
          if (result == 0)
            {
              errno = EILSEQ;
            }

          return false;
        }

      done += result;
    }

  if (memcmp(actual, expected, length) != 0)
    {
      errno = EILSEQ;
      return false;
    }

  do
    {
      result = read(fd, &extra, 1);
    }
  while (result < 0 && errno == EINTR);

  if (result != 0)
    {
      if (result > 0)
        {
          errno = EFBIG;
        }

      return false;
    }

  return true;
}

static bool stress_cycle(struct stress_worker_s *worker, uint64_t iteration,
                         uint8_t *expected, uint8_t *actual)
{
  static const size_t lengths[] =
    {1, 511, 512, 513, 1023, 4095, 4096, 4097, 8191, 16385, 65537};
  static const size_t offsets[] = {0, 495, 4095};
  struct stress_context_s *context = worker->context;
  struct stat status;
  struct dirent *entry;
  const char *stage = NULL;
  char source[144];
  char target[144];
  char name[24];
  uint32_t seed;
  size_t length;
  size_t offset;
  size_t patch;
  size_t i;
  int fd = -1;
  int found = 0;
  int saved_error = 0;
  DIR *dir = NULL;
  bool owned = false;
  bool moved = false;
  bool success = false;

#define STRESS_REQUIRE(label, condition) \
  do \
    { \
      if (!(condition)) \
        { \
          stage = label; \
          saved_error = errno; \
          goto out; \
        } \
    } \
  while (0)

  length = lengths[(iteration + worker->number) %
                   (sizeof(lengths) / sizeof(lengths[0]))];
  seed = STRESS_SEED ^ (worker->number * UINT32_C(0xaddba11)) ^
         (uint32_t)iteration ^ (uint32_t)(iteration >> 32);
  for (i = 0; i < length; i++)
    {
      expected[i] = stress_byte(seed, i);
    }

  snprintf(name, sizeof(name), "Case-%08" PRIx32, seed);
  snprintf(source, sizeof(source), "%s/%s", worker->incoming, name);
  snprintf(target, sizeof(target), "%s/%s", worker->committed, name);
  fd = open(source, O_CREAT | O_EXCL | O_RDWR, 0666);
  STRESS_REQUIRE("create", fd >= 0);
  owned = true;
  STRESS_REQUIRE("write", stress_write(fd, expected, length,
                                        257 + (seed & 4095)));

  /* Offset 495 crosses the 512-byte sector boundary; 4095 crosses a 4 KiB
   * boundary.  Small files still exercise a bounded in-place update.
   */

  offset = offsets[iteration % (sizeof(offsets) / sizeof(offsets[0]))];
  if (offset >= length)
    {
      offset = 0;
    }

  patch = length - offset < 67 ? length - offset : 67;
  for (i = offset; i < offset + patch; i++)
    {
      expected[i] = stress_byte(seed ^ UINT32_C(0xa5c37e19), i);
    }

  STRESS_REQUIRE("seek-patch", lseek(fd, offset, SEEK_SET) == (off_t)offset);
  STRESS_REQUIRE("write-patch", stress_write(fd, expected + offset,
                                              patch, 13));
  STRESS_REQUIRE("fsync", fsync(fd) == 0);
  STRESS_REQUIRE("fstat", fstat(fd, &status) == 0 &&
                           status.st_size == (off_t)length);
  STRESS_REQUIRE("seek-verify", lseek(fd, 0, SEEK_SET) == 0);
  STRESS_REQUIRE("verify-open", stress_verify(fd, expected, actual, length));
  saved_error = close(fd);
  fd = -1;
  STRESS_REQUIRE("close-written", saved_error == 0);
  STRESS_REQUIRE("rename", rename(source, target) == 0);
  moved = true;
  STRESS_REQUIRE("old-path-absent", stat(source, &status) < 0 &&
                                       errno == ENOENT);
  STRESS_REQUIRE("stat-renamed", stat(target, &status) == 0 &&
                                  status.st_size == (off_t)length);
  fd = open(target, O_RDONLY);
  STRESS_REQUIRE("reopen", fd >= 0);
  STRESS_REQUIRE("verify-reopened", stress_verify(fd, expected, actual,
                                                   length));
  saved_error = close(fd);
  fd = -1;
  STRESS_REQUIRE("close-reopened", saved_error == 0);

  dir = opendir(worker->committed);
  STRESS_REQUIRE("opendir", dir != NULL);
  errno = 0;
  while ((entry = readdir(dir)) != NULL)
    {
      if (strcmp(entry->d_name, ".") == 0 ||
          strcmp(entry->d_name, "..") == 0)
        {
          continue;
        }

      STRESS_REQUIRE("logical-directory-entry",
                     strcmp(entry->d_name, name) == 0);
      found++;
      errno = 0;
    }

  STRESS_REQUIRE("readdir-complete", errno == 0 && found == 1);
  saved_error = closedir(dir);
  dir = NULL;
  STRESS_REQUIRE("closedir", saved_error == 0);
  STRESS_REQUIRE("unlink", unlink(target) == 0);
  owned = false;
  STRESS_REQUIRE("deleted-path-absent", stat(target, &status) < 0 &&
                                           errno == ENOENT);

  pthread_mutex_lock(&context->lock);
  context->iterations++;
  context->worker_iterations[worker->number]++;
  context->written += length + patch;
  context->verified += length * 2;
  pthread_mutex_unlock(&context->lock);
  success = true;

out:
  if (!success)
    {
      stress_error(worker, stage, saved_error);
    }

  if (dir != NULL && closedir(dir) < 0)
    {
      stress_error(worker, "cleanup-closedir", errno);
    }

  if (fd >= 0 && close(fd) < 0)
    {
      stress_error(worker, "cleanup-close", errno);
    }

  if (owned && unlink(moved ? target : source) < 0)
    {
      stress_error(worker, "cleanup-unlink", errno);
    }

#undef STRESS_REQUIRE
  return success;
}

static void *stress_worker(void *argument)
{
  struct stress_worker_s *worker = argument;
  struct stress_context_s *context = worker->context;
  uint8_t *buffers;
  uint64_t start;
  uint64_t deadline;
  uint64_t iteration = 0;
  uint64_t now;
  bool stop;

  buffers = malloc(STRESS_MAX_FILE * 2);
  if (buffers == NULL)
    {
      stress_error(worker, "allocate-buffers", ENOMEM);
      return NULL;
    }

  start = stress_now_ms();
  if (start == UINT64_MAX)
    {
      stress_error(worker, "worker-start-clock", errno);
      free(buffers);
      return NULL;
    }

  /* Measure a full requested duration separately for each worker, excluding
   * setup and allocation.  The reporting thread joins both, even if its own
   * deadline expires a little earlier.
   */

  deadline = start + context->deadline_ms - context->start_ms;
  for (;;)
    {
      now = stress_now_ms();
      if (now == UINT64_MAX)
        {
          stress_error(worker, "monotonic-clock", errno);
          break;
        }

      pthread_mutex_lock(&context->lock);
      stop = context->stop;
      pthread_mutex_unlock(&context->lock);
      if (stop || now >= deadline)
        {
          break;
        }

      if (!stress_cycle(worker, iteration++, buffers,
                        buffers + STRESS_MAX_FILE))
        {
          break;
        }
    }

  now = stress_now_ms();
  if (now == UINT64_MAX)
    {
      stress_error(worker, "worker-end-clock", errno);
    }
  else
    {
      pthread_mutex_lock(&context->lock);
      context->worker_elapsed_ms[worker->number] = now - start;
      pthread_mutex_unlock(&context->lock);
    }

  free(buffers);
  return NULL;
}

static void stress_progress(struct stress_context_s *context, uint64_t now)
{
  struct mallinfo info = mallinfo();

  pthread_mutex_lock(&context->lock);
  printf("ENCFS_LONG_STRESS_PROGRESS elapsed_ms=%" PRIu64
         " iterations=%" PRIu64 " written_bytes=%" PRIu64
         " verified_bytes=%" PRIu64 " worker0_iterations=%" PRIu64
         " worker1_iterations=%" PRIu64
         " errors=%u heap_used=%u heap_free=%u\n",
         now - context->start_ms, context->iterations, context->written,
         context->verified, context->worker_iterations[0],
         context->worker_iterations[1], context->errors,
         info.uordblks, info.fordblks);
  pthread_mutex_unlock(&context->lock);
  fflush(stdout);
}

int encfs_test_stress(unsigned int seconds)
{
  struct stress_context_s context;
  struct stress_worker_s workers[STRESS_WORKERS];
  pthread_t threads[STRESS_WORKERS];
  pthread_attr_t attributes;
  struct mallinfo before;
  struct mallinfo after;
  struct timespec sleep_time = {1, 0};
  uint64_t now;
  uint64_t progress;
  uint64_t elapsed;
  size_t stacksize = 8192;
  unsigned int created = 0;
  unsigned int directories = 0;
  unsigned int i;
  int result;
  bool root_created = false;
  bool attr_created = false;
  bool stopped;
  bool complete;

  memset(&context, 0, sizeof(context));
  memset(workers, 0, sizeof(workers));
  context.start_ms = stress_now_ms();
  if (seconds < 60 || seconds > 86400 || context.start_ms == UINT64_MAX)
    {
      return -EINVAL;
    }

  result = pthread_mutex_init(&context.lock, NULL);
  if (result != 0)
    {
      return -result;
    }

  before = mallinfo();
  context.deadline_ms = context.start_ms + (uint64_t)seconds * 1000;
  snprintf(context.root, sizeof(context.root), STRESS_ROOT "-%ld-%" PRIu64,
           (long)getpid(), context.start_ms);
  for (i = 0; i < STRESS_WORKERS; i++)
    {
      workers[i].context = &context;
      workers[i].number = i;
      snprintf(workers[i].incoming, sizeof(workers[i].incoming),
               "%s/in-%u", context.root, i);
      snprintf(workers[i].committed, sizeof(workers[i].committed),
               "%s/out-%u", context.root, i);
    }

  printf("ENCFS_LONG_STRESS_START requested_seconds=%u workers=%u "
         "seed=%08" PRIx32 " max_live_file_bytes=%u root=%s\n",
         seconds, STRESS_WORKERS, STRESS_SEED,
         STRESS_WORKERS * STRESS_MAX_FILE, context.root);
  fflush(stdout);
  result = encfs_start();
  if (result < 0)
    {
      stress_error(&workers[0], "start-existing-volume", -result);
      goto done;
    }

  if (mkdir(context.root, 0777) < 0)
    {
      stress_error(&workers[0], "create-root", errno);
      goto done;
    }

  root_created = true;
  for (i = 0; i < STRESS_WORKERS; i++)
    {
      if (mkdir(workers[i].incoming, 0777) < 0)
        {
          stress_error(&workers[i], "create-incoming", errno);
          goto done;
        }

      directories++;
      if (mkdir(workers[i].committed, 0777) < 0)
        {
          stress_error(&workers[i], "create-committed", errno);
          goto done;
        }

      directories++;
    }

  result = pthread_attr_init(&attributes);
  if (result != 0)
    {
      stress_error(&workers[0], "thread-attributes", result);
      goto done;
    }

  attr_created = true;
#ifdef PTHREAD_STACK_MIN
  if (stacksize < (size_t)PTHREAD_STACK_MIN)
    {
      stacksize = PTHREAD_STACK_MIN;
    }
#endif

  result = pthread_attr_setstacksize(&attributes, stacksize);
  if (result != 0)
    {
      stress_error(&workers[0], "thread-stack", result);
      goto done;
    }

  for (i = 0; i < STRESS_WORKERS; i++)
    {
      result = pthread_create(&threads[i], &attributes, stress_worker,
                              &workers[i]);
      if (result != 0)
        {
          stress_error(&workers[i], "thread-create", result);
          goto done;
        }

      created++;
    }

  progress = context.start_ms + STRESS_PROGRESS_MS;
  for (;;)
    {
      now = stress_now_ms();
      if (now == UINT64_MAX)
        {
          stress_error(&workers[0], "progress-clock", errno);
          break;
        }

      pthread_mutex_lock(&context.lock);
      stopped = context.stop;
      pthread_mutex_unlock(&context.lock);
      if (stopped || now >= context.deadline_ms)
        {
          break;
        }

      if (now >= progress)
        {
          stress_progress(&context, now);
          progress = now + STRESS_PROGRESS_MS;
        }

      /* The workers continuously issue operations.  Only the reporting
       * thread sleeps; this is not a duty-cycled workload.
       */

      nanosleep(&sleep_time, NULL);
    }

done:
  for (i = 0; i < created; i++)
    {
      result = pthread_join(threads[i], NULL);
      if (result != 0)
        {
          stress_error(&workers[i], "thread-join", result);
        }
    }

  if (attr_created)
    {
      result = pthread_attr_destroy(&attributes);
      if (result != 0)
        {
          stress_error(&workers[0], "destroy-thread-attributes", result);
        }
    }

  for (i = 0; i < directories; i++)
    {
      if (rmdir((i & 1) ? workers[i / 2].committed :
                          workers[i / 2].incoming) < 0)
        {
          stress_error(&workers[i / 2], "remove-worker-directory", errno);
        }
    }

  if (root_created && rmdir(context.root) < 0)
    {
      stress_error(&workers[0], "remove-root", errno);
    }

  now = stress_now_ms();
  if (now == UINT64_MAX)
    {
      stress_error(&workers[0], "final-clock", errno);
      elapsed = 0;
    }
  else
    {
      elapsed = now - context.start_ms;
      stress_progress(&context, now);
    }

  after = mallinfo();
  complete = context.errors == 0 && created == STRESS_WORKERS &&
             elapsed >= (uint64_t)seconds * 1000 &&
             context.worker_iterations[0] > 0 &&
             context.worker_iterations[1] > 0 &&
             context.worker_elapsed_ms[0] >= (uint64_t)seconds * 1000 &&
             context.worker_elapsed_ms[1] >= (uint64_t)seconds * 1000;
  if (context.error_stage != NULL)
    {
      printf("ENCFS_LONG_STRESS_ERROR worker=%u stage=%s error=%d\n",
             context.error_worker, context.error_stage, context.error_code);
    }

  printf("ENCFS_LONG_STRESS_RESULT requested_seconds=%u elapsed_ms=%" PRIu64
         " workers=%u iterations=%" PRIu64 " written_bytes=%" PRIu64
         " verified_bytes=%" PRIu64 " worker0_iterations=%" PRIu64
         " worker1_iterations=%" PRIu64 " worker0_elapsed_ms=%" PRIu64
         " worker1_elapsed_ms=%" PRIu64 " errors=%u completed=%u "
         "heap_before=%u heap_after=%u\n",
         seconds, elapsed, created, context.iterations, context.written,
         context.verified, context.worker_iterations[0],
         context.worker_iterations[1], context.worker_elapsed_ms[0],
         context.worker_elapsed_ms[1], context.errors, complete ? 1 : 0,
         before.uordblks, after.uordblks);
  fflush(stdout);
  pthread_mutex_destroy(&context.lock);
  return complete ? 0 : -EIO;
}
