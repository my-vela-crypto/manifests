if(CONFIG_TESTING_ENCFS_EXTENDED)
  nuttx_add_application(NAME encfs_extended PRIORITY 100 STACKSIZE 16384
    SRCS encfs_extended_main.c)
  nuttx_add_application(NAME vela_fs_rwloops PRIORITY 100 STACKSIZE 16384
    SRCS ${NUTTX_DIR}/../tests/testcases/vela_fs_test/fs/stress/read_and_write_loops_test.c
    INCLUDE_DIRECTORIES ${NUTTX_DIR}/../tests/testcases/vela_fs_test/fs/include)
endif()
