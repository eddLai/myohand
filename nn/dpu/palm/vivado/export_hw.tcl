# Export what packaging needs, before the project moves.
#
# A Kria app is three files, and two of them are written from numbers that
# only the built design knows: the address the driver pokes and the interrupt
# it waits on. Reading them out now means the device tree overlay is written
# from the design rather than from a guess at what the defaults were.
#
#   vivado -mode batch -source export_hw.tcl
set proj /media/ntk/quant/yuechi_dpu_b1024/prj/dpu_kd240.xpr
set out  /media/ntk/quant/yuechi_dpu_b1024

open_project $proj
open_bd_design [get_files dpu_bd.bd]

puts "=== what the PS can address ==="
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps/Data]] {
    puts [format "  %-55s %s  %s" $seg [get_property OFFSET $seg] \
              [get_property RANGE $seg]]
}

puts "=== interrupt ==="
foreach n [get_bd_nets -of_objects [get_bd_pins dpu/dpu0_interrupt]] {
    puts "  net $n -> [get_bd_pins -of_objects [get_bd_nets $n]]"
}

puts "=== clocks as built ==="
foreach p {clk/clk_out1 clk/clk_out2 ps/pl_clk0} {
    puts [format "  %-16s %s Hz" $p [get_property CONFIG.FREQ_HZ [get_bd_pins $p]]]
}

# the XSA carries the bitstream and the hardware description together, which
# is what a later Vitis or device-tree flow wants to be handed
write_hw_platform -fixed -include_bit -force $out/dpu_kd240.xsa
puts "=== wrote $out/dpu_kd240.xsa ==="
exit 0
