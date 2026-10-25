include $(APPDIR)/Make.defs

PROGNAME = encfs_extended vela_fs_rwloops
PRIORITY = 100
STACKSIZE = 16384
MODULE = $(CONFIG_TESTING_ENCFS_EXTENDED)
MAINSRC = encfs_extended_main.c $(APPDIR)/tests/testcases/vela_fs_test/fs/stress/read_and_write_loops_test.c
CFLAGS += -I$(APPDIR)/tests/testcases/vela_fs_test/fs/include

include $(APPDIR)/Application.mk
