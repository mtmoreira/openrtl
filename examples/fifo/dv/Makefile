SIM ?= verilator
TOPLEVEL_LANG = verilog
TOPLEVEL = sync_fifo
MODULE = test_sync_fifo
VERILOG_SOURCES = $(abspath ../rtl/sync_fifo.sv)
COMPILE_ARGS += --trace --assert -GWIDTH=8 -GDEPTH=4
EXTRA_ARGS += --trace

include $(shell cocotb-config --makefiles)/Makefile.sim
