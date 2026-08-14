# Which architectures does this IP offer, and what does each cost?
# B1024 is the ceiling K24 can hold, so the parameter set has to be read off
# the IP rather than guessed from the naming convention.
create_project -in_memory -part xck24-ubva530-2LV-c
set_property ip_repo_paths /media/ntk/sda4/yuechi/dpu_ip/DPUCZDX8G_ip_repo_VAI_v3.0 [current_project]
update_ip_catalog -rebuild -quiet
create_ip -vlnv xilinx.com:ip:dpuczdx8g:4.1 -module_name dpu_cfg
set ip [get_ips dpu_cfg]
foreach p {CONFIG.ARCH CONFIG.ARCH_PP CONFIG.ARCH_ICP CONFIG.ARCH_OCP CONFIG.DPU_NUM_OF_CORE CONFIG.RAM_USAGE CONFIG.CONV_DSP_CASC_MAX CONFIG.DSP48_USAGE CONFIG.URAM_N} {
    if {[catch {list_property_value $p $ip} v]} { continue }
    puts "OPTIONS $p = $v"
}
puts "--- current ---"
foreach p [lsort [list_property $ip]] {
    if {[string match "CONFIG.*" $p]} { puts "  $p = [get_property $p $ip]" }
}
exit 0
