from __future__ import annotations
import collections
import json
import math
import os
import queue
import random
import socket
import struct
import threading
import time

import pygame

# =============================================================================
# Constants
# =============================================================================

HOST_IP = '192.168.50.1'
HOST_PORT = 55000

VIEWPORT_WIDTH = 320
VIEWPORT_HEIGHT = 240
FPS = 60

STATE_MENU = 'menu'
STATE_WAITING = 'waiting'
STATE_GAME = 'game'
STATE_POST_RACE = 'post_race'

TOTAL_LAPS = 3
MAX_PHYSICS_DT = 1.0 / 30.0

TILE_SIZE = 8

PLAYER_SIZE = 8
PLAYER_ACCEL = 450.0
PLAYER_DRAG = 2.0
PLAYER_MAX_SPEED_SAFETY = 500.0
PLAYER_RESTITUTION = 0.3
COLLISION_SLOP = 0.05
MAX_COLLISION_PASSES = 2
PLAYER_ANG_ACCEL = 10.0
PLAYER_ANG_DAMP = 5.0
PLAYER_MAX_ANG_VEL = 4.5

MOTOR_GPIO = 22
RUMBLE_DURATION = 0.12

BOOST_TILE_DELTA = 200.0
SLOWDOWN_TILE_DELTA = 200.0
TRACK_HISTORY_MAX = 10
TRACK_HISTORY_INTERVAL = 15
MINIMAP_W = 60
MINIMAP_H = 50
MINIMAP_MARGIN = 4
MINIMAP_SPRITE_SIZE = 8
POWERUP_SIZE_SCALE = 2.0
POWERUP_SIZE_DURATION = 5.0
SHELL_SIZE = PLAYER_SIZE
SHELL_SPEED = 300.0
RED_SHELL_TURN = math.pi
SPAWNER_RESPAWN_COOLDOWN = 5.0

FINISH_TILE_COUNT = 7

# =============================================================================
# Colors
# =============================================================================

COLOR_WHITE         = (255, 255, 255)
COLOR_BLACK         = (  0,   0,   0)
COLOR_GREY_LIGHT    = (160, 160, 160)
COLOR_GREY_MID      = (180, 180, 180)
COLOR_BACKGROUND    = ( 30,  30,  30)
COLOR_OPEN          = (255, 255, 255)
COLOR_WALL          = (  0,   0,   0)
COLOR_POWERUP1      = (255, 220,   0)
COLOR_POWERUP2      = (120, 220, 255)
COLOR_SPAWNER       = (180,  80, 220)
COLOR_SPAWNER_EMPTY = ( 80,  40,  80)
COLOR_SHELL_GREEN   = ( 40, 200,  40)
COLOR_SHELL_RED     = (220,  40,  40)
COLOR_PLAYER        = (220,  30,  30)
COLOR_PLAYER2       = ( 30, 100, 220)
COLOR_FINISH        = (180, 180, 180)
COLOR_ERROR         = (220,  60,  60)
COLOR_TRACK_P1      = (180, 120, 120)
COLOR_TRACK_P2      = (120, 120, 180)
BTN_BG              = ( 50,  50,  80)
BTN_BG_HOVER        = ( 80,  80, 130)
BTN_BORDER          = (120, 120, 180)
BTN_FG              = COLOR_WHITE

# =============================================================================
# Tile registry
# =============================================================================

TILE_TYPE_INFO = {
    'open':             {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'rect'},
    'wall':             {'color': COLOR_WALL, 'is_wall': True, 'shape': 'rect'},
    'powerup1':         {'color': COLOR_POWERUP1, 'is_wall': False, 'shape': 'rect'},
    'powerup2':         {'color': COLOR_POWERUP2, 'is_wall': False, 'shape': 'rect'},
    'spawner':          {'color': COLOR_SPAWNER, 'is_wall': False, 'shape': 'rect'},
    'player1_spawn':    {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'rect'},
    'player2_spawn':    {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'rect'},
    'finish_line':      {'color': COLOR_FINISH, 'is_wall': False, 'shape': 'rect'},
    'tri_top_left':     {'color': COLOR_WALL, 'is_wall': True, 'shape': 'tri',
                         'points': ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))},
    'tri_top_right':    {'color': COLOR_WALL, 'is_wall': True, 'shape': 'tri',
                         'points': ((1.0, 0.0), (1.0, 1.0), (0.0, 0.0))},
    'tri_bottom_left':  {'color': COLOR_WALL, 'is_wall': True, 'shape': 'tri',
                         'points': ((0.0, 1.0), (1.0, 1.0), (0.0, 0.0))},
    'tri_bottom_right': {'color': COLOR_WALL, 'is_wall': True, 'shape': 'tri',
                         'points': ((1.0, 1.0), (1.0, 0.0), (0.0, 1.0))},
    'arrow_left':  {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'arrow', 'direction': 'left'},
    'arrow_right': {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'arrow', 'direction': 'right'},
    'arrow_up':    {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'arrow', 'direction': 'up'},
    'arrow_down':  {'color': COLOR_OPEN, 'is_wall': False, 'shape': 'arrow', 'direction': 'down'},
}

CHAR_TO_TILE_TYPE = {
    '#': 'wall', '*': 'powerup1', '+': 'powerup2', '@': 'spawner',
    '1': 'player1_spawn', '2': 'player2_spawn', '|': 'finish_line',
    '<': 'arrow_left', '>': 'arrow_right', '^': 'arrow_up', 'v': 'arrow_down',
}

# =============================================================================
# Consumable powerup registry
# =============================================================================

POWERUP_SIZE_GROW = 'size_grow'
POWERUP_SIZE_SHRINK = 'size_shrink'
POWERUP_GREEN_SHELL = 'green_shell'
POWERUP_RED_SHELL = 'red_shell'

CONSUMABLE_POOL = [POWERUP_SIZE_GROW, POWERUP_SIZE_SHRINK, POWERUP_GREEN_SHELL, POWERUP_RED_SHELL]

POWERUP_LABEL = {
    POWERUP_SIZE_GROW:   'GROW',
    POWERUP_SIZE_SHRINK: 'SHRINK',
    POWERUP_GREEN_SHELL: 'GREEN',
    POWERUP_RED_SHELL:   'RED',
}

# =============================================================================
# Network helpers
# =============================================================================

# sentinel placed on a queue to signal thread shutdown
DISCONNECTED = object()


# drains a queue without processing items
def flush_queue(q: queue.Queue):
    while True:
        try: q.get_nowait()
        except queue.Empty: break


# sends a length-prefixed json payload over the socket
def send_msg(sock: socket.socket, payload: dict):
    data = json.dumps(payload).encode()
    sock.sendall(struct.pack('>I', len(data)) + data)


# blocks until a full length-prefixed json payload has been read
def recv_msg(sock: socket.socket) -> dict:
    def recv_exactly(n):
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise OSError('Connection closed.')
            buf += chunk
        return buf
    return json.loads(recv_exactly(struct.unpack('>I', recv_exactly(4))[0]))


# drains send_q and writes each message to the socket; signals disconnect on exit
def net_send_thread(sock: socket.socket, send_q: queue.Queue, signal_q: queue.Queue, log):
    log("send_thread started")
    try:
        while True:
            payload = send_q.get()
            if payload is DISCONNECTED:
                log("send_thread exiting")
                break
            send_msg(sock, payload)
    except OSError as e:
        try: log(f"send_thread OSError: {e}")
        except Exception: pass
    finally:
        try: signal_q.put(DISCONNECTED)
        except Exception: pass
        try: sock.close()
        except Exception: pass


# reads messages from the socket into recv_q; signals both signal queues on exit
def net_recv_thread(sock: socket.socket, recv_q: queue.Queue,
                    signal_q1: queue.Queue, signal_q2: queue.Queue, log):
    log("recv_thread started")
    try:
        while True:
            recv_q.put(recv_msg(sock))
    except OSError as e:
        try: log(f"recv_thread OSError: {e}")
        except Exception: pass
    finally:
        try: log("recv_thread exiting")
        except Exception: pass
        try: signal_q1.put(DISCONNECTED)
        except Exception: pass
        try: signal_q2.put(DISCONNECTED)
        except Exception: pass
        try: sock.close()
        except Exception: pass

# =============================================================================
# Sprite helpers
# =============================================================================

player_sprites: dict[str, pygame.Surface] = {}
arrow_sprites: dict[str, pygame.Surface] = {}

ARROW_ROTATION = {'right': 0, 'up': 90, 'left': 180, 'down': 270}


# loads and caches a player sprite by filename
def load_player_sprite(filename: str) -> pygame.Surface:
    sprite = player_sprites.get(filename)
    if sprite is None:
        sprite_path = os.path.join(os.path.dirname(__file__), filename)
        sprite = pygame.image.load(sprite_path).convert_alpha()
        player_sprites[filename] = sprite
    return sprite


# draws a player sprite scaled by size_multiplier; rotates by heading if heading is not None
def draw_player_sprite(surface: pygame.Surface, screen_x: int, screen_y: int,
                       color: tuple[int, int, int], size_multiplier: float,
                       heading: float | None = None):
    size = int(PLAYER_SIZE * size_multiplier)
    sprite = load_player_sprite('red_car.png' if color == COLOR_PLAYER else 'blue_car.png')
    sprite = pygame.transform.scale(sprite, (size, size))
    if heading is not None:
        sprite = pygame.transform.rotate(sprite, -math.degrees(heading) - 90.0)
    surface.blit(sprite, sprite.get_rect(center=(screen_x, screen_y)))


# loads and caches a directional arrow sprite, rotated to face the requested direction
def load_arrow_sprite(direction: str) -> pygame.Surface:
    sprite = arrow_sprites.get(direction)
    if sprite is None:
        path = os.path.join(os.path.dirname(__file__), 'arrow.png')
        base = pygame.image.load(path).convert_alpha()
        sprite = pygame.transform.rotate(base, ARROW_ROTATION[direction])
        arrow_sprites[direction] = sprite
    return sprite

# =============================================================================
# Physics helpers
# =============================================================================

# clamps value to the closed interval [low, high]
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


# returns (normal_x, normal_y, penetration) pushing rect_a out of rect_b, or None
def aabb_mtv(a: pygame.Rect, b: pygame.Rect) -> tuple[float, float, float] | None:
    if not a.colliderect(b):
        return None
    ox = min(a.right - b.left, b.right - a.left)
    oy = min(a.bottom - b.top, b.bottom - a.top)
    if ox <= 0.0 or oy <= 0.0:
        return None
    if ox < oy:
        return (-1.0 if a.centerx < b.centerx else 1.0, 0.0, ox)
    return (0.0, -1.0 if a.centery < b.centery else 1.0, oy)


# checks whether the player rect overlaps the triangular portion of a tri tile
def tri_mask_overlap(player_rect: pygame.Rect, tile_rect: pygame.Rect,
                     tile_type: str, tri_masks: dict,
                     player_mask: pygame.mask.Mask) -> bool:
    tri_mask = tri_masks.get(tile_type)
    if tri_mask is None:
        return False
    offset = (player_rect.left - tile_rect.left, player_rect.top - tile_rect.top)
    return tri_mask.overlap(player_mask, offset) is not None

# =============================================================================
# Game objects
# =============================================================================

class Tile:
    def __init__(self, tile_type: str, grid_x: int, grid_y: int):
        self.tile_type: str = tile_type
        self.world_rect: pygame.Rect = pygame.Rect(
            grid_x * TILE_SIZE, grid_y * TILE_SIZE, TILE_SIZE, TILE_SIZE)


class Map:
    def __init__(self, filepath: str):
        with open(filepath) as f:
            lines = f.read().splitlines()

        self.rows: int = len(lines)
        self.cols: int = max(len(l) for l in lines)
        self.pixel_width: int = self.cols * TILE_SIZE
        self.pixel_height: int = self.rows * TILE_SIZE
        self.grid: list[list[Tile]] = []
        self.spawner_positions: list[tuple[float, float]] = []
        self.player1_spawn: tuple[float, float] = (0.0, 0.0)
        self.player2_spawn: tuple[float, float] = (0.0, 0.0)
        self.finish_line_x: float = 0.0
        self.finish_line_y_min: float = 0.0
        self.finish_line_y_max: float = 0.0
        self.interior: set[tuple[int, int]] = set()

        finish_tiles = self.parse_grid(lines)
        self.validate_finish_line(finish_tiles)
        self.infer_diagonal_tiles()
        self.compute_interior()

    # parses lines into self.grid and collects spawn/finish/spawner positions
    def parse_grid(self, lines: list[str]) -> list[tuple[int, int]]:
        finish_tiles: list[tuple[int, int]] = []
        p1_spawn: tuple[float, float] | None = None
        p2_spawn: tuple[float, float] | None = None
        for r, line in enumerate(lines):
            row: list[Tile] = []
            for c in range(self.cols):
                ch = line[c] if c < len(line) else ' '
                tt = CHAR_TO_TILE_TYPE.get(ch, 'open')
                center = (c * TILE_SIZE + TILE_SIZE / 2, r * TILE_SIZE + TILE_SIZE / 2)
                if tt == 'player1_spawn':
                    p1_spawn = center
                elif tt == 'player2_spawn':
                    p2_spawn = center
                elif tt == 'finish_line':
                    finish_tiles.append((r, c))
                elif tt == 'spawner':
                    self.spawner_positions.append(center)
                row.append(Tile(tt, c, r))
            self.grid.append(row)
        if p1_spawn is None:
            raise ValueError("Map missing '1' spawn.")
        if p2_spawn is None:
            raise ValueError("Map missing '2' spawn.")
        self.player1_spawn = p1_spawn
        self.player2_spawn = p2_spawn
        return finish_tiles

    # confirms the finish line is a single contiguous vertical column of the expected length
    def validate_finish_line(self, finish_tiles: list[tuple[int, int]]):
        if len(finish_tiles) != FINISH_TILE_COUNT:
            raise ValueError(
                f"Map needs {FINISH_TILE_COUNT} '|' finish tiles, found {len(finish_tiles)}.")
        finish_cols = {c for _, c in finish_tiles}
        if len(finish_cols) != 1:
            raise ValueError("Finish tiles must form a single vertical column.")
        finish_rows = sorted(r for r, _ in finish_tiles)
        if any(b != a + 1 for a, b in zip(finish_rows, finish_rows[1:])):
            raise ValueError("Finish tiles must be contiguous.")
        fc = next(iter(finish_cols))
        self.finish_line_x = fc * TILE_SIZE + TILE_SIZE / 2
        self.finish_line_y_min = finish_rows[0] * TILE_SIZE
        self.finish_line_y_max = (finish_rows[-1] + 1) * TILE_SIZE

    # marching squares: replaces wall corners with diagonal tri tiles
    def infer_diagonal_tiles(self):
        walls = [[t.tile_type == 'wall' for t in row] for row in self.grid]
        for r in range(self.rows - 1):
            for c in range(self.cols - 1):
                if walls[r][c] and walls[r + 1][c + 1]:
                    self.grid[r][c + 1].tile_type = 'tri_bottom_left'
                    self.grid[r + 1][c].tile_type = 'tri_top_right'
                if walls[r][c + 1] and walls[r + 1][c]:
                    self.grid[r][c].tile_type = 'tri_bottom_right'
                    self.grid[r + 1][c + 1].tile_type = 'tri_top_left'

    # bfs from p1 spawn through non-wall (or tri) tiles to mark interior coordinates
    def compute_interior(self):
        sc = int(self.player1_spawn[0] // TILE_SIZE)
        sr = int(self.player1_spawn[1] // TILE_SIZE)
        self.interior = {(sr, sc)}
        stack = [(sr, sc)]
        while stack:
            r, c = stack.pop()
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if (nr, nc) in self.interior or not (0 <= nr < self.rows and 0 <= nc < self.cols):
                    continue
                tt = self.grid[nr][nc].tile_type
                info = TILE_TYPE_INFO.get(tt, TILE_TYPE_INFO['open'])
                if info['is_wall'] and not tt.startswith('tri_'):
                    continue
                self.interior.add((nr, nc))
                stack.append((nr, nc))

    # returns True if the world coordinate falls inside the playable region
    def is_interior(self, world_x: float, world_y: float) -> bool:
        return (int(world_y) // TILE_SIZE, int(world_x) // TILE_SIZE) in self.interior

    # bfs outward from (world_x, world_y) to find center of nearest interior tile
    def nearest_interior_center(self, world_x: float, world_y: float) -> tuple[float, float]:
        start_r = int(world_y) // TILE_SIZE
        start_c = int(world_x) // TILE_SIZE
        if (start_r, start_c) in self.interior:
            return (start_c * TILE_SIZE + TILE_SIZE / 2, start_r * TILE_SIZE + TILE_SIZE / 2)
        visited = {(start_r, start_c)}
        q = [(start_r, start_c)]
        while q:
            next_q = []
            for r, c in q:
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = r + dr, c + dc
                    if (nr, nc) in visited or not (0 <= nr < self.rows and 0 <= nc < self.cols):
                        continue
                    visited.add((nr, nc))
                    if (nr, nc) in self.interior:
                        return (nc * TILE_SIZE + TILE_SIZE / 2, nr * TILE_SIZE + TILE_SIZE / 2)
                    next_q.append((nr, nc))
            q = next_q
        return self.player1_spawn

    # returns all tiles whose grid cells overlap the given world rect
    def get_tiles_in_rect(self, world_rect: pygame.Rect) -> list[Tile]:
        c0 = max(0, world_rect.left // TILE_SIZE)
        c1 = min(self.cols, world_rect.right // TILE_SIZE + 1)
        r0 = max(0, world_rect.top // TILE_SIZE)
        r1 = min(self.rows, world_rect.bottom // TILE_SIZE + 1)
        return [self.grid[r][c] for r in range(r0, r1) for c in range(c0, c1)]

    # rasterises the full map into a single transparent surface for camera blitting
    def build_surface(self) -> pygame.Surface:
        surf = pygame.Surface((self.pixel_width, self.pixel_height), pygame.SRCALPHA)
        for row in self.grid:
            for tile in row:
                info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                wr = tile.world_rect
                is_interior = (wr.top // TILE_SIZE, wr.left // TILE_SIZE) in self.interior
                if info['shape'] == 'rect':
                    fill = info['color'] if info['is_wall'] or is_interior else COLOR_BACKGROUND
                    pygame.draw.rect(surf, fill, wr)
                elif info['shape'] == 'arrow':
                    pygame.draw.rect(surf, COLOR_OPEN if is_interior else COLOR_BACKGROUND, wr)
                    if is_interior:
                        sprite = load_arrow_sprite(info['direction'])
                        surf.blit(sprite, sprite.get_rect(center=wr.center))
                else:
                    pygame.draw.rect(surf, COLOR_OPEN if is_interior else COLOR_BACKGROUND, wr)
                    m = TILE_SIZE - 1
                    pts = [(int(wr.x + px * m), int(wr.y + py * m)) for px, py in info['points']]
                    pygame.draw.polygon(surf, info['color'], pts)
        return surf


class Camera:
    def __init__(self, viewport_w: int, viewport_h: int):
        self.viewport_w: int = viewport_w
        self.viewport_h: int = viewport_h
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self.heading: float = 0.0

    # positions the viewport so the world coordinate is at its center
    def center_on(self, world_x: float, world_y: float):
        self.offset_x = world_x - self.viewport_w / 2
        self.offset_y = world_y - self.viewport_h / 2


# green shell travels backward in a straight line; red shell homes toward target
class Shell:
    def __init__(self, shell_type: str, world_x: float, world_y: float,
                 heading: float, owner_index: int):
        self.shell_type: str = shell_type
        self.world_x: float = world_x
        self.world_y: float = world_y
        self.heading: float = heading
        self.vel_x: float = math.cos(heading) * SHELL_SPEED
        self.vel_y: float = math.sin(heading) * SHELL_SPEED
        self.owner_index: int = owner_index
        self.bounces_remaining: int = 3

    # axis-aligned bounding box centered on the shell's world position
    def get_bounding_rect(self) -> pygame.Rect:
        h = SHELL_SIZE // 2
        return pygame.Rect(int(self.world_x) - h, int(self.world_y) - h, SHELL_SIZE, SHELL_SIZE)

    # advances position; red shells curve toward (target_x, target_y)
    def update(self, dt: float, target_x: float, target_y: float):
        if self.shell_type == POWERUP_RED_SHELL:
            dx, dy = target_x - self.world_x, target_y - self.world_y
            if dx or dy:
                diff = (math.atan2(dy, dx) - self.heading + math.pi) % (2 * math.pi) - math.pi
                self.heading += clamp(diff, -RED_SHELL_TURN * dt, RED_SHELL_TURN * dt)
                self.vel_x = math.cos(self.heading) * SHELL_SPEED
                self.vel_y = math.sin(self.heading) * SHELL_SPEED
        self.world_x += self.vel_x * dt
        self.world_y += self.vel_y * dt

    # returns (nx, ny) wall normal if colliding with a wall tile, else None
    def hits_wall(self, game_map: Map, tri_masks: dict,
                  player_mask: pygame.mask.Mask) -> tuple[float, float] | None:
        rect = self.get_bounding_rect()
        for tile in game_map.get_tiles_in_rect(rect):
            info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
            if not info['is_wall']:
                continue
            if info['shape'] == 'tri' and not tri_mask_overlap(
                    rect, tile.world_rect, tile.tile_type, tri_masks, player_mask):
                continue
            contact = aabb_mtv(rect, tile.world_rect)
            return (contact[0], contact[1]) if contact else (1.0, 0.0)
        return None

    # checks whether the shell rect overlaps the vehicle rect
    def hits_vehicle(self, vehicle: Vehicle) -> bool:
        return self.get_bounding_rect().colliderect(vehicle.get_bounding_rect())

    # draws the shell as a colored square with a black outline
    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        color = COLOR_SHELL_GREEN if self.shell_type == POWERUP_GREEN_SHELL else COLOR_SHELL_RED
        h = SHELL_SIZE // 2
        rect = pygame.Rect(screen_x - h, screen_y - h, SHELL_SIZE, SHELL_SIZE)
        pygame.draw.rect(surface, color, rect)
        pygame.draw.rect(surface, COLOR_BLACK, rect, 1)

    # serialises the shell to a network-friendly dict
    def to_dict(self) -> dict:
        return {'type': self.shell_type,
                'x': round(self.world_x, 3),
                'y': round(self.world_y, 3)}


# reconstructs a display-only Shell from a network dict (heading and owner are unused on client)
def shell_from_dict(data: dict) -> Shell:
    return Shell(data['type'], float(data['x']), float(data['y']), 0.0, 0)


class PowerupSpawner:
    def __init__(self, world_x: float, world_y: float):
        self.world_x: float = world_x
        self.world_y: float = world_y
        self.available_powerup: str | None = random.choice(CONSUMABLE_POOL)
        self.cooldown_timer: float = 0.0

    # axis-aligned bounding box centered on the spawner's world position
    def get_bounding_rect(self) -> pygame.Rect:
        h = TILE_SIZE // 2
        return pygame.Rect(int(self.world_x) - h, int(self.world_y) - h, TILE_SIZE, TILE_SIZE)

    # ticks the respawn cooldown and re-rolls a powerup when it expires
    def update(self, dt: float):
        if self.available_powerup is None:
            self.cooldown_timer = max(0.0, self.cooldown_timer - dt)
            if self.cooldown_timer == 0.0:
                self.available_powerup = random.choice(CONSUMABLE_POOL)

    # transfers powerup to vehicle if eligible; returns True on success
    def try_collect(self, vehicle: Vehicle) -> bool:
        if self.available_powerup is None or vehicle.stored_powerup is not None:
            return False
        if not self.get_bounding_rect().colliderect(vehicle.get_bounding_rect()):
            return False
        vehicle.stored_powerup = self.available_powerup
        self.available_powerup = None
        self.cooldown_timer = SPAWNER_RESPAWN_COOLDOWN
        return True

    # draws the spawner; dim color while empty, full color while a powerup is available
    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        h = TILE_SIZE // 2
        rect = pygame.Rect(screen_x - h, screen_y - h, TILE_SIZE, TILE_SIZE)
        color = COLOR_SPAWNER if self.available_powerup else COLOR_SPAWNER_EMPTY
        pygame.draw.rect(surface, color, rect)
        pygame.draw.rect(surface, COLOR_BLACK, rect, 1)

    # serialises the spawner to a network-friendly dict
    def to_dict(self) -> dict:
        return {'x': round(self.world_x, 3), 'y': round(self.world_y, 3),
                'occupied': self.available_powerup is not None}


# reconstructs a display-only PowerupSpawner from a network dict
def spawner_from_dict(data: dict) -> PowerupSpawner:
    sp = PowerupSpawner(float(data['x']), float(data['y']))
    sp.available_powerup = 'occupied' if data.get('occupied') else None
    return sp


class Vehicle:
    def __init__(self, start_x: float, start_y: float,
                 throttle_forward_key: int, throttle_back_key: int,
                 steer_left_key: int, steer_right_key: int,
                 color: tuple[int, int, int]):
        self.world_x: float = start_x
        self.world_y: float = start_y
        self.vel_x: float = 0.0
        self.vel_y: float = 0.0
        self.throttle_input: float = 0.0
        self.steer_input: float = 0.0
        self.heading: float = 0.0
        self.ang_vel: float = 0.0
        self.prev_world_x: float = start_x
        self.prev_world_y: float = start_y
        self.curr_lap: int = 1
        self.max_lap: int = 1
        self.race_finished: bool = False
        self.finish_place: int | None = None
        self.ignore_first_forward_cross: bool = True
        self.throttle_forward_key: int = throttle_forward_key
        self.throttle_back_key: int = throttle_back_key
        self.steer_left_key: int = steer_left_key
        self.steer_right_key: int = steer_right_key
        self.color: tuple[int, int, int] = color
        self.stored_powerup: str | None = None
        self.size_multiplier: float = 1.0
        self.size_timer: float = 0.0
        self.on_powerup1: bool = False
        self.on_powerup2: bool = False
        self.track_history: collections.deque = collections.deque(maxlen=TRACK_HISTORY_MAX)
        self.track_frame_counter: int = 0

    # axis-aligned bounding box scaled by size_multiplier
    def get_bounding_rect(self) -> pygame.Rect:
        size = int(PLAYER_SIZE * self.size_multiplier)
        h = size // 2
        return pygame.Rect(int(self.world_x) - h, int(self.world_y) - h, size, size)

    # reads keyboard state into throttle/steer inputs (debug / keyboard mode)
    def handle_input(self):
        keys = pygame.key.get_pressed()
        self.throttle_input = float(int(keys[self.throttle_forward_key]) -
                                    int(keys[self.throttle_back_key]))
        self.steer_input = float(int(keys[self.steer_right_key]) -
                                 int(keys[self.steer_left_key]))

    # integrates angular and linear velocity, applies drag, and advances position
    def update_physics(self, dt: float):
        self.prev_world_x, self.prev_world_y = self.world_x, self.world_y
        if self.size_timer > 0.0:
            self.size_timer = max(0.0, self.size_timer - dt)
            if self.size_timer <= 0.0:
                self.size_multiplier = 1.0
        ang_acc = PLAYER_ANG_ACCEL * self.steer_input - PLAYER_ANG_DAMP * self.ang_vel
        self.ang_vel = clamp(self.ang_vel + ang_acc * dt, -PLAYER_MAX_ANG_VEL, PLAYER_MAX_ANG_VEL)
        self.heading = (self.heading + self.ang_vel * dt) % (2.0 * math.pi)
        fwd = PLAYER_ACCEL * self.throttle_input
        self.vel_x = clamp(self.vel_x + (fwd * math.cos(self.heading) - PLAYER_DRAG * self.vel_x) * dt,
                           -PLAYER_MAX_SPEED_SAFETY, PLAYER_MAX_SPEED_SAFETY)
        self.vel_y = clamp(self.vel_y + (fwd * math.sin(self.heading) - PLAYER_DRAG * self.vel_y) * dt,
                           -PLAYER_MAX_SPEED_SAFETY, PLAYER_MAX_SPEED_SAFETY)
        self.world_x += self.vel_x * dt
        self.world_y += self.vel_y * dt

    # iteratively pushes the vehicle out of overlapping wall tiles; returns True if any correction was applied
    def resolve_collisions(self, game_map: Map, tri_masks: dict,
                           player_mask: pygame.mask.Mask) -> bool:
        any_corrected = False
        for _ in range(MAX_COLLISION_PASSES):
            corrected = False
            for tile in game_map.get_tiles_in_rect(self.get_bounding_rect()):
                info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                if not info['is_wall']:
                    continue
                rect = self.get_bounding_rect()
                if info['shape'] == 'tri' and not tri_mask_overlap(
                        rect, tile.world_rect, tile.tile_type, tri_masks, player_mask):
                    continue
                contact = aabb_mtv(rect, tile.world_rect)
                if contact is None:
                    continue
                nx, ny, pen = contact
                # push out by penetration plus slop, then reflect velocity along the normal
                self.world_x += nx * (pen + COLLISION_SLOP)
                self.world_y += ny * (pen + COLLISION_SLOP)
                vn = self.vel_x * nx + self.vel_y * ny
                if vn < 0.0:
                    b = (1.0 + PLAYER_RESTITUTION) * vn
                    self.vel_x -= b * nx
                    self.vel_y -= b * ny
                corrected = True
            any_corrected = any_corrected or corrected
            if not corrected:
                break
        if abs(self.vel_x) < 1e-3:
            self.vel_x = 0.0
        if abs(self.vel_y) < 1e-3:
            self.vel_y = 0.0
        return any_corrected

    # advances the per-frame counter and appends to track_history when it overflows the interval
    def tick_track_history(self) -> bool:
        self.track_frame_counter += 1
        if self.track_frame_counter < TRACK_HISTORY_INTERVAL:
            return False
        self.track_frame_counter = 0
        self.track_history.append((self.world_x, self.world_y))
        return True

    # draws the rotated player sprite at the screen position
    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        draw_player_sprite(surface, screen_x, screen_y, self.color, self.size_multiplier,
                           self.heading)


# serialises a vehicle's display-relevant state into the network packet shape
def vehicle_to_dict(v: Vehicle) -> dict:
    return {'x': round(v.world_x, 3), 'y': round(v.world_y, 3),
            'heading': round(v.heading, 5), 'lap': v.max_lap,
            'place': v.finish_place, 'powerup': v.stored_powerup,
            'size': round(v.size_multiplier, 4)}

# =============================================================================
# Render helpers
# =============================================================================

# constructs a centered Rect for a UI button
def make_button_rect(center_x: int, center_y: int, width: int = 110, height: int = 40) -> pygame.Rect:
    r = pygame.Rect(0, 0, width, height)
    r.center = (center_x, center_y)
    return r


# draws a labelled menu-style button; selected highlights with hover color
def button(screen: pygame.Surface, rect: pygame.Rect, label: str, font: pygame.font.Font,
           selected: bool = False, bg: tuple = BTN_BG, bg_h: tuple = BTN_BG_HOVER,
           fg: tuple = BTN_FG, border: tuple = BTN_BORDER, bw: int = 3):
    pygame.draw.rect(screen, bg_h if selected else bg, rect)
    pygame.draw.rect(screen, border, rect, bw)
    s = font.render(label, True, fg)
    screen.blit(s, s.get_rect(center=rect.center))


# blits a surface centered on the given point
def blit_centered(screen: pygame.Surface, surf: pygame.Surface, center: tuple[int, int]):
    screen.blit(surf, surf.get_rect(center=center))


# draws a white box with a black outline and centered text inside
def text_box(screen: pygame.Surface, surf: pygame.Surface, box: pygame.Rect, border: int = 2):
    pygame.draw.rect(screen, COLOR_WHITE, box)
    pygame.draw.rect(screen, COLOR_BLACK, box, border)
    screen.blit(surf, surf.get_rect(center=box.center))


# renders a centered status screen with the title and a message line
def render_status_screen(screen: pygame.Surface, title_font: pygame.font.Font,
                         button_font: pygame.font.Font, message: str):
    screen.fill((25, 25, 25))
    blit_centered(screen, title_font.render('PiKart', True, COLOR_WHITE),
                  (VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 5))
    blit_centered(screen, button_font.render(message, True, COLOR_GREY_MID),
                  (VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2))


# renders the main menu (Play / Quit) with optional error text
def render_menu(screen: pygame.Surface, play_rect: pygame.Rect, quit_rect: pygame.Rect,
                title_font: pygame.font.Font, button_font: pygame.font.Font,
                error_msg: str | None, selected_idx: int = 0):
    screen.fill((25, 25, 25))
    blit_centered(screen, title_font.render('PiKart', True, COLOR_WHITE),
                  (VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 5))
    if error_msg:
        blit_centered(screen, button_font.render(error_msg, True, COLOR_ERROR),
                      (VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 55))
    button(screen, play_rect, 'Play', button_font, selected_idx == 0)
    button(screen, quit_rect, 'Quit', button_font, selected_idx == 1)


# renders the post-race overlay with optional Play Again button
def render_post_race(screen: pygame.Surface, play_again_rect: pygame.Rect, menu_rect: pygame.Rect,
                     title_font: pygame.font.Font, button_font: pygame.font.Font,
                     selected_idx: int = 0, show_play_again: bool = True):
    overlay = pygame.Surface((VIEWPORT_WIDTH, VIEWPORT_HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    screen.blit(overlay, (0, 0))
    blit_centered(screen, title_font.render('Race Over!', True, COLOR_WHITE),
                  (VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 80))
    if show_play_again:
        button(screen, play_again_rect, 'Play Again', button_font, selected_idx == 0)
    button(screen, menu_rect, 'Exit to Menu', button_font,
           selected_idx == (1 if show_play_again else 0))


# scales map_surface to fit the minimap box, preserving aspect ratio
def build_minimap_surface(map_surface: pygame.Surface, game_map: Map) -> pygame.Surface:
    scale = min(MINIMAP_W / game_map.pixel_width, MINIMAP_H / game_map.pixel_height)
    w = max(1, int(game_map.pixel_width * scale))
    h = max(1, int(game_map.pixel_height * scale))
    base = pygame.Surface((w, h))
    base.fill(COLOR_BACKGROUND)
    scaled = pygame.transform.scale(map_surface, (w, h))
    base.blit(scaled, (0, 0))
    return base


# draws the mini-map with a 1px border and rotated vehicle sprites in the bottom-right corner
def render_minimap(screen: pygame.Surface, minimap_surface: pygame.Surface,
                   vehicles: list, game_map: Map, viewport_rect: pygame.Rect):
    mw = minimap_surface.get_width()
    mh = minimap_surface.get_height()
    scale_x = mw / game_map.pixel_width
    scale_y = mh / game_map.pixel_height
    x = viewport_rect.right - MINIMAP_MARGIN - mw
    y = viewport_rect.bottom - MINIMAP_MARGIN - mh
    pygame.draw.rect(screen, COLOR_GREY_LIGHT, (x - 1, y - 1, mw + 2, mh + 2))
    screen.blit(minimap_surface, (x, y))
    sprite_scale = MINIMAP_SPRITE_SIZE / PLAYER_SIZE
    for v in vehicles:
        dx = int(v.world_x * scale_x)
        dy = int(v.world_y * scale_y)
        draw_player_sprite(screen, x + dx, y + dy, v.color, sprite_scale, v.heading)


# renders a centered text box overlay (used for the countdown and "GO!" message)
def render_center_overlay_message(screen: pygame.Surface, message: str, font: pygame.font.Font):
    surf = font.render(message, True, COLOR_BLACK)
    px, py = 12, 6
    box = pygame.Rect(VIEWPORT_WIDTH // 2 - (surf.get_width() + 2 * px) // 2,
                      VIEWPORT_HEIGHT // 2 - (surf.get_height() + 2 * py) // 2,
                      surf.get_width() + 2 * px, surf.get_height() + 2 * py)
    text_box(screen, surf, box, border=3)


# renders the rotating camera view: blits the map, optional overlays, and rotates around the camera center
def render_map(screen: pygame.Surface, map_surface: pygame.Surface, camera: Camera,
               viewport_rect: pygame.Rect | None = None,
               overlay_vehicles: list | None = None,
               overlay_shells: list | None = None,
               overlay_spawners: list | None = None,
               track_histories: list[tuple[collections.deque, tuple]] | None = None):
    if viewport_rect is None:
        viewport_rect = pygame.Rect(0, 0, camera.viewport_w, camera.viewport_h)
    diag = int(math.ceil(math.sqrt(camera.viewport_w ** 2 + camera.viewport_h ** 2))) + 2 * TILE_SIZE
    patch = pygame.Surface((diag, diag), pygame.SRCALPHA)
    cx = camera.offset_x + camera.viewport_w / 2.0
    cy = camera.offset_y + camera.viewport_h / 2.0
    plx, pty = cx - diag / 2.0, cy - diag / 2.0
    patch.blit(map_surface, (-int(round(plx)), -int(round(pty))))
    # draw track marks for each player onto the patch in patch-space coordinates
    if track_histories:
        for history, color in track_histories:
            for wx, wy in history:
                pygame.draw.rect(patch, color, (int(wx - plx) - 1, int(wy - pty) - 1, 2, 2))
    # draw spawners under vehicles, vehicles under shells
    for group in (overlay_spawners, overlay_vehicles, overlay_shells):
        if group:
            for obj in group:
                obj.draw(patch, int(round(obj.world_x - plx)), int(round(obj.world_y - pty)))
    rotated = pygame.transform.rotate(patch, math.degrees(camera.heading) + 90.0)
    screen.blit(rotated, rotated.get_rect(center=viewport_rect.center))


# renders the in-game HUD: lap counter, total/lap timers, optional finish placement, and stored powerup
def render_hud(screen: pygame.Surface, viewport_rect: pygame.Rect,
               max_lap: int, total_laps: int, total_timer: float, lap_timer: float,
               finish_place: int | None, hud_font: pygame.font.Font,
               place_font: pygame.font.Font, stored_powerup: str | None = None):
    w, h, m = 46, 22, 5
    lap_box = pygame.Rect(viewport_rect.right - m - w, viewport_rect.top + m, w, h)
    text_box(screen, hud_font.render(f"{min(max_lap, total_laps)}/{total_laps}", True, COLOR_BLACK),
             lap_box)

    def fmt(t):
        return f"{int(t // 60)}:{t % 60:05.2f}"

    ts = hud_font.render(f"T: {fmt(total_timer)}", True, (0, 140, 0))
    ls = hud_font.render(f"L: {fmt(lap_timer)}", True, (0, 140, 0))
    cw, px, py = max(ts.get_width(), ls.get_width()), 5, 3
    box = pygame.Rect(viewport_rect.centerx - (cw + 2 * px) // 2, viewport_rect.top + m,
                      cw + 2 * px, ts.get_height() + ls.get_height() + 2 + 2 * py)
    pygame.draw.rect(screen, COLOR_WHITE, box)
    pygame.draw.rect(screen, COLOR_BLACK, box, 1)
    screen.blit(ts, ts.get_rect(centerx=box.centerx, top=box.top + py))
    screen.blit(ls, ls.get_rect(centerx=box.centerx, top=box.top + py + ts.get_height() + 2))

    if finish_place is not None:
        p = finish_place
        suf = 'th' if 10 <= p % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(p % 10, 'th')
        s = place_font.render(f"{p}{suf}", True, COLOR_BLACK)
        text_box(screen, s,
                 pygame.Rect(viewport_rect.centerx - (s.get_width() + 14) // 2,
                             viewport_rect.centery - 20,
                             s.get_width() + 14, s.get_height() + 10))

    label = POWERUP_LABEL.get(stored_powerup, 'NONE') if stored_powerup else 'NONE'
    s = hud_font.render(label, True, COLOR_BLACK)
    pw = max(s.get_width() + 6, w)
    powerup_box = pygame.Rect(viewport_rect.right - m - pw, lap_box.bottom + 3, pw, h)
    text_box(screen, s, powerup_box)

# =============================================================================
# Vibration motor output (pigpio, GPIO 22)
#
# Wiring:
#   GPIO 22 (Pin 15) -> 1k resistor -> NPN transistor Base
#   Transistor Collector -> Motor (-)
#   Motor (+) -> 3.3V or 5V
#   Transistor Emitter -> GND
#   Flyback diode across motor terminals
# =============================================================================

# pigpio.pi instance, set by motor_init
motor_pi: object = None
# monotonic timestamp when the current pulse should turn off
motor_end_time: float = 0.0


# connects pigpio and configures the motor GPIO pin as output
def motor_init():
    global motor_pi
    import pigpio
    motor_pi = pigpio.pi()
    if not motor_pi.connected:
        raise RuntimeError("pigpiod not running — start with: sudo pigpiod")
    motor_pi.set_mode(MOTOR_GPIO, pigpio.OUTPUT)
    motor_pi.write(MOTOR_GPIO, 0)


# turns off the motor and disconnects pigpio
def motor_stop():
    global motor_pi
    if motor_pi is not None:
        motor_pi.write(MOTOR_GPIO, 0)
        motor_pi.stop()
        motor_pi = None


# triggers a single fixed-duration rumble pulse, extending the deadline only if the new one is later
def motor_rumble():
    global motor_end_time
    deadline = time.monotonic() + RUMBLE_DURATION
    if deadline > motor_end_time:
        motor_end_time = deadline
    if motor_pi is not None:
        motor_pi.write(MOTOR_GPIO, 1)


# called once per frame; turns the motor off once the pulse has elapsed
def motor_update():
    global motor_end_time
    if motor_pi is not None and motor_end_time > 0.0 and time.monotonic() >= motor_end_time:
        motor_pi.write(MOTOR_GPIO, 0)
        motor_end_time = 0.0


# immediately cancels any active rumble and turns the motor off
def motor_cancel():
    global motor_end_time
    motor_end_time = 0.0
    if motor_pi is not None:
        motor_pi.write(MOTOR_GPIO, 0)


# =============================================================================
# Joystick input (MCP3008 via bit-banged SPI, pigpio)
#
# Wiring:
#   MCP3008 CLK  -> GPIO 5    MCP3008 MOSI -> GPIO 6
#   MCP3008 MISO -> GPIO 13   MCP3008 CS   -> GPIO 19
#   Joystick VRx -> CH0 (steer)   VRy -> CH1 (throttle)
#   P1 button -> GPIO 27 (active low)   P2 button -> GPIO 17 (active low)
# =============================================================================

JS_CLK = 5
JS_MOSI = 6
JS_MISO = 13
JS_CS = 19
JS_SW = 27

JS_SPI_DELAY = 0.00001
JS_POLL_HZ = 100
JS_DEADZONE = 8
JS_CENTRE = 256
JS_MENU_TRIGGER = 80
JS_MENU_NEUTRAL = 30

# module-level joystick state, raw ADC values (0-512)
js_pi = None
js_lock = threading.Lock()
js_x_raw = JS_CENTRE
js_y_raw = JS_CENTRE
js_menu_x_armed = True
js_menu_y_armed = True
js_running = False
js_prev_pressed: bool = False
js_press_latched: bool = False


# bit-bangs the SPI sequence to read a 10-bit value from one MCP3008 channel
def js_read_mcp3008(channel: int) -> int:
    pi = js_pi
    pi.write(JS_CS, 0)
    time.sleep(JS_SPI_DELAY)
    for bit in [1, 1, (channel >> 2) & 1, (channel >> 1) & 1, channel & 1]:
        pi.write(JS_MOSI, bit)
        time.sleep(JS_SPI_DELAY)
        pi.write(JS_CLK, 1)
        time.sleep(JS_SPI_DELAY)
        pi.write(JS_CLK, 0)
        time.sleep(JS_SPI_DELAY)
    pi.write(JS_CLK, 1)
    time.sleep(JS_SPI_DELAY)
    pi.write(JS_CLK, 0)
    time.sleep(JS_SPI_DELAY)
    result = 0
    for _ in range(10):
        pi.write(JS_CLK, 1)
        time.sleep(JS_SPI_DELAY)
        result = (result << 1) | pi.read(JS_MISO)
        pi.write(JS_CLK, 0)
        time.sleep(JS_SPI_DELAY)
    pi.write(JS_CS, 1)
    return result


# converts raw value to [-1.0, 1.0] with deadzone applied in raw units
def js_to_float(raw: int) -> float:
    v = raw - JS_CENTRE
    if abs(v) < JS_DEADZONE:
        return 0.0
    return max(-1.0, min(1.0, v / JS_CENTRE))


# polling loop run by the joystick thread; updates raw values and latches button rising edges
def js_poll_loop():
    global js_x_raw, js_y_raw, js_prev_pressed, js_press_latched
    interval = 1.0 / JS_POLL_HZ
    while js_running:
        x = js_read_mcp3008(0)
        y = js_read_mcp3008(1)
        try:
            pressed = (js_pi.read(JS_SW) == 0)
        except Exception:
            pressed = False
        with js_lock:
            # detect rising edge and latch it
            if pressed and not js_prev_pressed:
                js_press_latched = True
            js_prev_pressed = pressed
            js_x_raw = x
            js_y_raw = y
        time.sleep(interval)


# initialises pigpio, configures pins, and starts the polling thread
def joystick_init():
    global js_pi, js_running
    import pigpio
    js_pi = pigpio.pi()
    if not js_pi.connected:
        raise RuntimeError("pigpiod not running — start with: sudo pigpiod")
    pin_modes = ((JS_CLK, pigpio.OUTPUT), (JS_MOSI, pigpio.OUTPUT),
                 (JS_MISO, pigpio.INPUT), (JS_CS, pigpio.OUTPUT),
                 (JS_SW, pigpio.INPUT))
    for pin, mode in pin_modes:
        js_pi.set_mode(pin, mode)
    js_pi.set_pull_up_down(JS_SW, pigpio.PUD_UP)
    js_pi.write(JS_CS, 1)
    js_pi.write(JS_CLK, 0)
    js_running = True
    threading.Thread(target=js_poll_loop, daemon=True).start()


# stops the polling thread and disconnects pigpio
def joystick_stop():
    global js_running
    js_running = False
    if js_pi:
        js_pi.stop()


# returns the negated y-axis as throttle in [-1.0, 1.0]
def joystick_throttle() -> float:
    with js_lock:
        return -js_to_float(js_y_raw)


# returns the x-axis as steering in [-1.0, 1.0]
def joystick_steer() -> float:
    with js_lock:
        return js_to_float(js_x_raw)


# reads the button state directly from GPIO, bypassing the poll thread's latch
def joystick_btn(sw_pin: int) -> bool:
    if js_pi is None:
        return False
    return js_pi.read(sw_pin) == 0


# atomically consumes a latched joystick press; returns True once per physical press
def joystick_consume_press() -> bool:
    global js_press_latched
    with js_lock:
        if js_press_latched:
            js_press_latched = False
            return True
        return False


# clears any latched press without consuming it (used on state transitions)
def joystick_clear_latch() -> None:
    global js_press_latched
    with js_lock:
        js_press_latched = False


# returns -1, 0, or +1 for a menu move on Y; re-arms after returning to neutral
def joystick_menu_y() -> int:
    global js_menu_y_armed
    with js_lock:
        y = js_y_raw
    if js_menu_y_armed:
        if y < JS_CENTRE - JS_MENU_TRIGGER:
            js_menu_y_armed = False
            return -1
        if y > JS_CENTRE + JS_MENU_TRIGGER:
            js_menu_y_armed = False
            return 1
    elif abs(y - JS_CENTRE) < JS_MENU_NEUTRAL:
        js_menu_y_armed = True
    return 0


# returns -1, 0, or +1 for a menu move on X; re-arms after returning to neutral
def joystick_menu_x() -> int:
    global js_menu_x_armed
    with js_lock:
        x = js_x_raw
    if js_menu_x_armed:
        if x < JS_CENTRE - JS_MENU_TRIGGER:
            js_menu_x_armed = False
            return -1
        if x > JS_CENTRE + JS_MENU_TRIGGER:
            js_menu_x_armed = False
            return 1
    elif abs(x - JS_CENTRE) < JS_MENU_NEUTRAL:
        js_menu_x_armed = True
    return 0