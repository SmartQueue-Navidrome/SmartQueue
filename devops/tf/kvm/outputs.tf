output "floating_ip" {
  description = "Public floating IP for node1 (jump host)"
  value       = openstack_networking_floatingip_v2.node1_fip.address
}

output "node_private_ips" {
  description = "Private IPs for all nodes"
  value       = var.private_ips
}

output "node_names" {
  description = "Instance names"
  value       = openstack_compute_instance_v2.nodes[*].name
}

output "node_ids" {
  description = "Instance IDs"
  value       = openstack_compute_instance_v2.nodes[*].id
}

output "volume_devices" {
  description = "Block volume attachment device paths"
  value = {
    navidrome = openstack_compute_volume_attach_v2.navidrome_attach.device
    postgres  = openstack_compute_volume_attach_v2.postgres_attach.device
  }
}
