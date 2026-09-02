SIM ?= verilator
TOPLEVEL_LANG = verilog
TOPLEVEL = skid_buffer
MODULE = test_skid_buffer
VERILOG_SOURCES = $(abspath ../rtl/skid_buffer.sv)
COMPILE_ARGS += --trace --assert -GWIDTH=8
EXTRA_ARGS += --trace
include $(shell cocotb-config --makefiles)/Makefile.sim
