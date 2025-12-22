# SmokePing Maintenance Guide

This guide provides commands and procedures for maintaining, cleaning up, and troubleshooting SmokePing deployments.

## 🛑 Container Management

### Stop Containers

**Stop all SmokePing containers at once:**
```bash
docker stop $(docker ps -a | grep smokeping | awk '{print $1}')
```

**Stop specific edition containers:**
```bash
# Basic Edition
docker stop smokeping-basic

# Standard Edition
docker stop smokeping-standard

# Pro Edition
docker stop smokeping-pro
```

**Stop tunnel containers:**
```bash
docker stop $(docker ps -a | grep tunnel | awk '{print $1}')
```

### Remove Containers

**Remove all SmokePing containers:**
```bash
docker rm $(docker ps -a | grep smokeping | awk '{print $1}')
```

**Remove tunnel containers:**
```bash
docker rm $(docker ps -a | grep tunnel | awk '{print $1}')
```

**Force remove stuck containers:**
```bash
# Force remove specific container
docker rm -f tunnel-smokeping

# Force remove all SmokePing-related containers
docker rm -f $(docker ps -a | grep smokeping | awk '{print $1}')
```

## 🗑️ Clean Up Resources

### Docker Volumes

**Remove specific SmokePing volumes:**
```bash
# Basic Edition volumes
docker volume rm smokeping-basic-config smokeping-basic-data

# Standard Edition volumes
docker volume rm smokeping-standard-config smokeping-standard-data

# Pro Edition volumes
docker volume rm smokeping-pro-config smokeping-pro-data
```

⚠️ **WARNING**: Removing volumes will delete all historical monitoring data!

**Prune all unused volumes:**
```bash
docker volume prune -f
```

### Docker Networks

**Remove specific networks:**
```bash
docker network rm basic_smokeping-net
docker network rm standard_smokeping-net
docker network rm pro_smokeping-net
```

**Prune unused networks:**
```bash
docker network prune -f
```

## 🧹 Complete Cleanup

### Edition-Specific Cleanup

Stop and remove all resources for a specific edition:

```bash
# Basic Edition
docker-compose -f editions/basic/docker-compose.yml down -v

# Standard Edition
docker-compose -f editions/standard/docker-compose.yml down -v

# Pro Edition
docker-compose -f editions/pro/docker-compose.yml down -v
```

### Nuclear Option

⚠️ **DANGER**: This removes ALL Docker resources system-wide, not just SmokePing!

```bash
# Remove ALL containers, images, volumes, and networks
docker system prune -a --volumes -f
```

## 🔧 Troubleshooting

### Network Still In Use Error

If you get "network has active endpoints" error:

```bash
# 1. Find what's using the network
docker network inspect basic_smokeping-net | grep -A 10 "Containers"

# 2. Force remove the container using it
docker rm -f tunnel-smokeping

# 3. Now remove the network
docker network rm basic_smokeping-net
```

### Container Won't Stop

For stubborn containers:

```bash
# Force kill by container ID
docker kill $(docker ps -q --filter "name=smokeping")

# Then remove
docker rm $(docker ps -aq --filter "name=smokeping")
```

### Check Resource Usage

Monitor Docker resource usage:

```bash
# Check disk usage
docker system df

# List all volumes with sizes
docker volume ls -q | xargs docker volume inspect | grep -E "Name|Mountpoint" | paste - -

# Check container logs size
du -sh /var/lib/docker/containers/*/*-json.log | sort -h
```

## 🔄 Restart Services

### Quick Restart

```bash
# Restart specific edition
cd editions/basic && docker-compose restart
cd editions/standard && docker-compose restart
cd editions/pro && docker-compose restart
```

### Full Restart (Recreate Containers)

```bash
# Basic Edition
cd editions/basic
docker-compose down
docker-compose up -d

# With fresh config
docker-compose down -v
./setup.sh
```

## 📋 Best Practices

1. **Before cleanup**: Always backup important data using the migration scripts
2. **Regular maintenance**: Prune unused resources weekly to save disk space
3. **Log rotation**: Configure Docker log rotation to prevent disk fill
4. **Monitor disk usage**: Keep an eye on volume sizes, especially for Pro edition

## 🚨 Emergency Recovery

If SmokePing won't start after cleanup:

1. Check for port conflicts:
   ```bash
   netstat -tulpn | grep -E "8080|80|5432|8086|3000"
   ```

2. Reset Docker networking:
   ```bash
   docker network ls | grep smokeping | awk '{print $1}' | xargs docker network rm
   systemctl restart docker
   ```

3. Restore from backup:
   ```bash
   ./shared/scripts/restore.sh <backup-directory>
   ```

## 🔗 Related Documentation

- [Migration Guide](./migration.md) - Backup and restore procedures
- [Quick Tunnels](./quick-tunnels.md) - Tunnel management
- [Troubleshooting](../../README.md#-troubleshooting) - Common issues