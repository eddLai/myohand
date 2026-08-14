# Out-of-context synthesis of the DPU alone, to learn whether K24 holds it.
#
# PG338 puts B1024 at 34,074 LUT and 230 DSP against the 70,560 and 360 this
# part has, but that table is written for other devices and the MNIST work on
# this same board found csynth LUT estimates pessimistic by a factor of two.
# Synthesis out of context answers for this part specifically, and does it
# without the PS side, the interconnect or a floorplan getting in the way of
# reading the number.
#
# Deliberately not implementation: placement and routing on a part this full
# takes hours, and if synthesis already overflows there is nothing to route.
#
#   vivado -mode batch -source synth_b1024.tcl
set part xck24-ubva530-2LV-c
set root /media/ntk/sda4/yuechi/dpu_ip
set proj $root/synth_b1024

file delete -force $proj
create_project dpu_synth $proj -part $part -force
set_property ip_repo_paths $root/DPUCZDX8G_ip_repo_VAI_v3.0 [current_project]
update_ip_catalog -rebuild -quiet

create_ip -vlnv xilinx.com:ip:dpuczdx8g:4.1 -module_name dpu_b1024
set_property -dict [list CONFIG.ARCH {1024}] [get_ips dpu_b1024]
generate_target all [get_ips dpu_b1024]

# one core, out of context, so the report is the DPU and nothing else
create_ip_run [get_ips dpu_b1024]
set run [get_runs dpu_b1024_synth_1]
set_property -dict [list STEPS.SYNTH_DESIGN.ARGS.FLATTEN_HIERARCHY rebuilt] $run
launch_runs $run -jobs 4
wait_on_run $run

open_run $run -name dpu_ooc
puts "=== utilisation against xck24 ==="
report_utilization -file $root/dpu_b1024_util.rpt
foreach line [split [report_utilization -return_string] "\n"] {
    if {[regexp {\|\s*(CLB LUTs|CLB Registers|Block RAM Tile|DSPs|URAM|CARRY8)\s*\|} $line]} { puts $line }
}
puts "=== timing (out of context, no board constraints) ==="
report_timing_summary -file $root/dpu_b1024_timing.rpt
puts "reports written to $root"
exit 0
