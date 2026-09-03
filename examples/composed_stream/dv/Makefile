SIM = verilator
TOPLEVEL_LANG = verilog
TOPLEVEL = fifo_skid_stream
MODULE = test_composed_stream
# The trusted runner supplies the exact three verified source paths.
COMPILE_ARGS += --trace --assert
EXTRA_ARGS += --trace
include $(shell cocotb-config --makefiles)/Makefile.sim
