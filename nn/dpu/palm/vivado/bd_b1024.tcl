# Full KD240 design: PS + DPUCZDX8G B1024, through to a bitstream.
#
# The out-of-context synthesis answered "does the DPU fit on this part". This
# answers the question that actually gates a board test: does it fit *with*
# the PS, the interconnect and a floorplan, and does it close timing.
#
# Clock choice is deliberately low. The palm detector runs only on re-detect,
# perhaps once a second, so a slower DPU costs almost nothing here, while a
# lower clock is the single biggest lever on whether place-and-route closes
# on the first attempt. 200/400 first; raising it later is a re-run, whereas
# starting high and failing costs the same hours with nothing to show.
#
#   vivado -mode batch -source bd_b1024.tcl
#
set part    xck24-ubva530-2LV-c
set board   xilinx.com:kd240_som:part0:1.1
set iprepo  /media/ntk/sda4/yuechi/dpu_ip/DPUCZDX8G_ip_repo_VAI_v3.0
set root    /media/ntk/quant/yuechi_dpu_b1024
set proj    $root/prj
set F1      200.000
set F2      400.000
set JOBS    16

# a build that dies at hour four because someone else filled the disk costs
# the same as a build that never started, so refuse to start without room
set free_kb [lindex [exec df -Pk $root] end-2]
if {$free_kb < 20000000} {
    puts "ABORT: only [expr {$free_kb/1048576}] GB free on $root, want 20 GB"
    exit 1
}
puts "disk: [expr {$free_kb/1048576}] GB free on $root"

file delete -force $proj
create_project dpu_kd240 $proj -part $part -force
set_property BOARD_PART $board [current_project]
set_property ip_repo_paths $iprepo [current_project]
update_ip_catalog -rebuild -quiet

create_bd_design dpu_bd

# ---------------------------------------------------------------- PS
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
    -config {apply_board_preset "1"} [get_bd_cells ps]

# HPM0 FPD carries the control writes; HP0/1/2 carry instructions and the two
# data streams, one master per port so the three do not share bandwidth
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__MAXIGP0__DATA_WIDTH {32} \
    CONFIG.PSU__USE__S_AXI_GP2 {1} \
    CONFIG.PSU__USE__S_AXI_GP3 {1} \
    CONFIG.PSU__USE__S_AXI_GP4 {1} \
    CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
    CONFIG.PSU__SAXIGP3__DATA_WIDTH {128} \
    CONFIG.PSU__SAXIGP4__DATA_WIDTH {128} \
    CONFIG.PSU__USE__IRQ0 {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
] [get_bd_cells ps]

# ---------------------------------------------------------------- clocks
# The DPU needs its 2x clock exactly double and phase aligned, which is why
# both come out of one MMCM rather than out of two PS PL clocks: PS clocks
# are timed as unrelated groups even when they divide from the same PLL.
#
# 99.999001 rather than 100: the PS divides a 1.5 GHz VCO by an integer and
# reports what it actually achieved, and Vivado compares the two FREQ_HZ
# properties for exact equality, so asking for a round 100 fails validation.
create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz clk
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {99.999001} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $F1 \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ $F2 \
    CONFIG.USE_LOCKED {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
    CONFIG.RESET_PORT {resetn} \
] [get_bd_cells clk]

connect_bd_net [get_bd_pins ps/pl_clk0]     [get_bd_pins clk/clk_in1]
connect_bd_net [get_bd_pins ps/pl_resetn0]  [get_bd_pins clk/resetn]
# PRIM_IN_FREQ rounds to 3 places internally, so state the exact figure on the
# pin as well; validation reads the pin, not the parameter
set_property CONFIG.FREQ_HZ 99999001 [get_bd_pins clk/clk_in1]

foreach {name clkpin} {rst_ps ps/pl_clk0 rst_1x clk/clk_out1 rst_2x clk/clk_out2} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset $name
    connect_bd_net [get_bd_pins $clkpin]       [get_bd_pins $name/slowest_sync_clk]
    connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins $name/ext_reset_in]
}
connect_bd_net [get_bd_pins clk/locked] [get_bd_pins rst_1x/dcm_locked]
connect_bd_net [get_bd_pins clk/locked] [get_bd_pins rst_2x/dcm_locked]

# ---------------------------------------------------------------- DPU
create_bd_cell -type ip -vlnv xilinx.com:ip:dpuczdx8g:4.1 dpu
# ARCH is the only knob worth setting; v4.1 has no DPU_NUM and defaults to one
# core, which is what the fingerprint the compiler already matched describes
set_property -dict [list CONFIG.ARCH {1024}] [get_bd_cells dpu]

connect_bd_net [get_bd_pins ps/pl_clk0]        [get_bd_pins dpu/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins dpu/s_axi_aresetn]
connect_bd_net [get_bd_pins clk/clk_out1]      [get_bd_pins dpu/m_axi_dpu_aclk]
connect_bd_net [get_bd_pins rst_1x/peripheral_aresetn] [get_bd_pins dpu/m_axi_dpu_aresetn]
connect_bd_net [get_bd_pins clk/clk_out2]      [get_bd_pins dpu/dpu_2x_clk]
connect_bd_net [get_bd_pins rst_2x/peripheral_aresetn] [get_bd_pins dpu/dpu_2x_resetn]
connect_bd_net [get_bd_pins dpu/dpu0_interrupt] [get_bd_pins ps/pl_ps_irq0]

# ---------------------------------------------------------------- fabric
# SmartConnect rather than direct nets: the DPU masters and the PS ports sit
# in different clock domains, and letting the tool insert the crossing is one
# fewer hand-written thing that can be subtly wrong.
proc sc {name si mi clk rst} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect $name
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells $name]
    connect_bd_intf_net [get_bd_intf_pins $si] [get_bd_intf_pins $name/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins $name/M00_AXI] [get_bd_intf_pins $mi]
    connect_bd_net [get_bd_pins $clk] [get_bd_pins $name/aclk]
    connect_bd_net [get_bd_pins $rst] [get_bd_pins $name/aresetn]
}

sc sc_ctrl  ps/M_AXI_HPM0_FPD  dpu/S_AXI \
    ps/pl_clk0 rst_ps/peripheral_aresetn
sc sc_instr dpu/DPU0_M_AXI_INSTR ps/S_AXI_HP0_FPD \
    clk/clk_out1 rst_1x/peripheral_aresetn
sc sc_data0 dpu/DPU0_M_AXI_DATA0 ps/S_AXI_HP1_FPD \
    clk/clk_out1 rst_1x/peripheral_aresetn
sc sc_data1 dpu/DPU0_M_AXI_DATA1 ps/S_AXI_HP2_FPD \
    clk/clk_out1 rst_1x/peripheral_aresetn

# the SmartConnects straddle two domains, so each needs the second clock too
foreach n {sc_ctrl sc_instr sc_data0 sc_data1} {
    set extra [get_bd_pins -quiet $n/aclk1]
    if {[llength $extra]} { connect_bd_net [get_bd_pins clk/clk_out1] $extra }
}
foreach n {sc_instr sc_data0 sc_data1} {
    set extra [get_bd_pins -quiet $n/aclk1]
    if {[llength $extra]} { connect_bd_net [get_bd_pins ps/pl_clk0] $extra }
}

connect_bd_net [get_bd_pins ps/maxihpm0_fpd_aclk] [get_bd_pins ps/pl_clk0]
foreach p {saxihp0_fpd_aclk saxihp1_fpd_aclk saxihp2_fpd_aclk} {
    set pin [get_bd_pins -quiet ps/$p]
    if {[llength $pin]} { connect_bd_net [get_bd_pins clk/clk_out1] $pin }
}

assign_bd_address
validate_bd_design
save_bd_design

# ---------------------------------------------------------------- build
make_wrapper -files [get_files $proj/dpu_kd240.srcs/sources_1/bd/dpu_bd/dpu_bd.bd] -top
add_files -norecurse $proj/dpu_kd240.gen/sources_1/bd/dpu_bd/hdl/dpu_bd_wrapper.v
set_property top dpu_bd_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs $JOBS
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {
    puts "ABORT: synthesis failed"
    exit 1
}
puts "=== synthesis done ==="

launch_runs impl_1 -to_step write_bitstream -jobs $JOBS
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    puts "ABORT: implementation failed, see $proj/dpu_kd240.runs/impl_1"
    exit 1
}

open_run impl_1
report_utilization      -file $root/impl_util.rpt
report_timing_summary   -file $root/impl_timing.rpt
set wns [get_property SLACK [get_timing_paths -delay_type max]]
set whs [get_property SLACK [get_timing_paths -delay_type min]]
puts "=== WNS $wns ns   WHS $whs ns  (both must be >= 0) ==="
foreach line [split [report_utilization -return_string] "\n"] {
    if {[regexp {\|\s*(CLB LUTs|CLB Registers|Block RAM Tile|DSPs|URAM)\s*\|} $line]} { puts $line }
}
puts "=== bitstream ==="
puts [glob -nocomplain $proj/dpu_kd240.runs/impl_1/*.bit]
exit 0
