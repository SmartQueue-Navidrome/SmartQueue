# Block storage volumes for persistent data (Lab 4-1 pattern)
# All volumes attached to node1 where stateful services run

resource "openstack_blockstorage_volume_v3" "navidrome" {
  name = "vol-navidrome-${var.suffix}"
  size = 5 # GiB
}

resource "openstack_blockstorage_volume_v3" "postgres" {
  name = "vol-postgres-${var.suffix}"
  size = 5 # GiB
}

# Attach volumes to node1
resource "openstack_compute_volume_attach_v2" "navidrome_attach" {
  instance_id = openstack_compute_instance_v2.node1.id
  volume_id   = openstack_blockstorage_volume_v3.navidrome.id
}

resource "openstack_compute_volume_attach_v2" "postgres_attach" {
  instance_id = openstack_compute_instance_v2.node1.id
  volume_id   = openstack_blockstorage_volume_v3.postgres.id
}
