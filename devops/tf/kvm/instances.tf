# Look up the image
data "openstack_images_image_v2" "ubuntu" {
  name        = var.image
  most_recent = true
}

# K8S nodes (single-node all-in-one setup)
resource "openstack_compute_instance_v2" "nodes" {
  count     = var.node_count
  name      = "smartqueue-node${count.index + 1}-${var.suffix}"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  # Chameleon KVM: Blazar lease creates a custom flavor with reservation_id as its ID
  flavor_id = var.reservation_id
  key_pair  = var.key_pair

  # Public network (sharednet1)
  network {
    name = data.openstack_networking_network_v2.sharednet1.name
  }

  # Private network (fixed IP via pre-created port)
  network {
    port = openstack_networking_port_v2.private_port[count.index].id
  }

  security_groups = [
    "default",
    openstack_networking_secgroup_v2.allow_ssh.name,
    openstack_networking_secgroup_v2.allow_http.name,
    openstack_networking_secgroup_v2.allow_k8s.name,
  ]
}
