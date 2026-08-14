# Can Vivado 2025.1 read a DPU IP that was encrypted with the 2022.2 tool?
# The IP declares support for 2022.2 only, and Xilinx keys are meant to be
# forward compatible, but nobody has published a run of this pairing. A
# project that only adds the repository and instantiates the core answers it
# in seconds instead of after a synthesis.
set part xck24-ubva530-2LV-c
set proj /media/ntk/sda4/yuechi/dpu_ip/probe_prj
file delete -force $proj
create_project probe $proj -part $part -force
set_property ip_repo_paths /media/ntk/sda4/yuechi/dpu_ip/DPUCZDX8G_ip_repo_VAI_v3.0 [current_project]
update_ip_catalog -rebuild

puts "=== catalog search ==="
set hits [get_ipdefs -filter {NAME =~ "*dpuczdx8g*"}]
puts "matches: $hits"
if {[llength $hits] == 0} {
    puts "RESULT: the catalogue does not see the IP"
    exit 2
}
foreach h $hits {
    puts "  vlnv        [get_property VLNV $h]"
    puts "  upgrade     [get_property UPGRADE_VERSIONS $h]"
}

puts "=== instantiate ==="
if {[catch {create_ip -vlnv [get_property VLNV [lindex $hits 0]] -module_name dpu_probe} err]} {
    puts "RESULT: create_ip failed: $err"
    exit 3
}
puts "RESULT: instantiated"
puts "  arch options: [llength [list_property [get_ips dpu_probe]]] properties"
foreach p {CONFIG.DPU_NUM CONFIG.ARCH CONFIG.SYS_IP_TYPE} {
    if {![catch {get_property $p [get_ips dpu_probe]} v]} { puts "  $p = $v" }
}
puts "=== arch-like properties ==="
foreach p [list_property [get_ips dpu_probe]] {
    if {[string match "*ARCH*" $p] || [string match "*B1024*" $p]} { puts "  $p = [get_property $p [get_ips dpu_probe]]" }
}
exit 0
