# Ask the IP for B1024 and read back what it derived.
# K24 holds 70,560 LUT and 360 DSP, and PG338 puts B1024 at 34,074 / 230,
# so this is the largest configuration with room for the PS side as well.
create_project -in_memory -part xck24-ubva530-2LV-c
set_property ip_repo_paths /media/ntk/sda4/yuechi/dpu_ip/DPUCZDX8G_ip_repo_VAI_v3.0 [current_project]
update_ip_catalog -rebuild -quiet
create_ip -vlnv xilinx.com:ip:dpuczdx8g:4.1 -module_name dpu_b1024
set ip [get_ips dpu_b1024]
set_property -dict [list CONFIG.ARCH {1024}] $ip
puts "=== after asking for 1024 ==="
foreach p {CONFIG.ARCH CONFIG.ARCH_PP CONFIG.ARCH_ICP CONFIG.ARCH_OCP CONFIG.CONV_DSP_NUM CONFIG.ALU_DSP_NUM CONFIG.DPU_NUM CONFIG.URAM_N CONFIG.CONV_LEAKYRELU CONFIG.ALU_LEAKYRELU} {
    if {![catch {get_property $p $ip} v]} { puts "  $p = $v" }
}
puts "=== fingerprint the compiler must match ==="
foreach p [lsort [list_property $ip]] {
    if {[string match "*FINGER*" $p] || [string match "*TARGET*" $p] || [string match "*ISA*" $p]} {
        puts "  $p = [get_property $p $ip]"
    }
}
exit 0
