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

import pygame

# =============================================================================
# Constants
# =============================================================================

HOST_IP   = '192.168.50.1'
HOST_PORT = 55000

VIEWPORT_WIDTH  = 320
VIEWPORT_HEIGHT = 240
FPS             = 60

STATE_MENU      = 'menu'
STATE_WAITING   = 'waiting'
STATE_GAME      = 'game'
STATE_POST_RACE = 'post_race'

TOTAL_LAPS     = 3
MAX_PHYSICS_DT = 1.0 / 30.0

TILE_SIZE = 8

PLAYER_SIZE             = 8
PLAYER_ACCEL            = 450.0
PLAYER_DRAG             = 2.0
PLAYER_MAX_SPEED_SAFETY = 500.0
PLAYER_RESTITUTION      = 0.3
COLLISION_SLOP          = 0.05
MAX_COLLISION_PASSES    = 2
PLAYER_ANG_ACCEL        = 10.0
PLAYER_ANG_DAMP         = 5.0
PLAYER_MAX_ANG_VEL      = 4.5

MOTOR_GPIO       = 22
RUMBLE_DURATION  = 0.12

BOOST_TILE_DELTA         = 200.0
SLOWDOWN_TILE_DELTA      = 200.0
TRACK_HISTORY_MAX        = 10
TRACK_HISTORY_INTERVAL   = 15
MINIMAP_W                = 60
MINIMAP_H                = 50
MINIMAP_MARGIN           = 4
POWERUP_SIZE_SCALE       = 2.0
POWERUP_SIZE_DURATION    = 5.0
SHELL_SIZE               = PLAYER_SIZE
SHELL_SPEED              = 300.0
RED_SHELL_TURN           = math.pi
SPAWNER_RESPAWN_COOLDOWN = 5.0

# =============================================================================
# Colors
# =============================================================================

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

_PLAYER_SPRITE_CACHE: dict[str, pygame.Surface] = {}


def _load_player_sprite(filename: str) -> pygame.Surface:
    sprite = _PLAYER_SPRITE_CACHE.get(filename)
    if sprite is None:
        sprite_path = os.path.join(os.path.dirname(__file__), filename)
        sprite = pygame.image.load(sprite_path).convert_alpha()
        _PLAYER_SPRITE_CACHE[filename] = sprite
    return sprite


def _draw_player_sprite(surface: pygame.Surface, screen_x: int, screen_y: int,
                        color: tuple[int, int, int], size_multiplier: float,
                        heading: float):
    size = int(PLAYER_SIZE * size_multiplier)
    sprite = _load_player_sprite('red_car.png' if color == COLOR_PLAYER else 'blue_car.png')
    sprite = pygame.transform.scale(sprite, (size, size))
    angle = -math.degrees(heading) - 90.0
    sprite = pygame.transform.rotate(sprite, angle)
    surface.blit(sprite, sprite.get_rect(center=(screen_x, screen_y)))


def _draw_player_sprite_static(surface: pygame.Surface, screen_x: int, screen_y: int,
                               color: tuple[int, int, int], size_multiplier: float):
    size = int(PLAYER_SIZE * size_multiplier)
    sprite = _load_player_sprite('red_car.png' if color == COLOR_PLAYER else 'blue_car.png')
    sprite = pygame.transform.scale(sprite, (size, size))
    surface.blit(sprite, sprite.get_rect(center=(screen_x, screen_y)))


_ARROW_SPRITE_CACHE: dict[str, pygame.Surface] = {}

_ARROW_ROTATION = {'right': 0, 'up': 90, 'left': 180, 'down': 270}


def _load_arrow_sprite(direction: str) -> pygame.Surface:
    sprite = _ARROW_SPRITE_CACHE.get(direction)
    if sprite is None:
        path = os.path.join(os.path.dirname(__file__), 'arrow.png')
        base = pygame.image.load(path).convert_alpha()
        sprite = pygame.transform.rotate(base, _ARROW_ROTATION[direction])
        _ARROW_SPRITE_CACHE[direction] = sprite
    return sprite


# =============================================================================
# Tile registry
# =============================================================================

TILE_TYPE_INFO = {
    'open':             {'color': COLOR_OPEN,    'is_wall': False, 'shape': 'rect'},
    'wall':             {'color': COLOR_WALL,    'is_wall': True,  'shape': 'rect'},
    'powerup1':         {'color': COLOR_POWERUP1,'is_wall': False, 'shape': 'rect'},
    'powerup2':         {'color': COLOR_POWERUP2,'is_wall': False, 'shape': 'rect'},
    'spawner':          {'color': COLOR_SPAWNER, 'is_wall': False, 'shape': 'rect'},
    'player1_spawn':    {'color': COLOR_OPEN,    'is_wall': False, 'shape': 'rect'},
    'player2_spawn':    {'color': COLOR_OPEN,    'is_wall': False, 'shape': 'rect'},
    'finish_line':      {'color': COLOR_FINISH,  'is_wall': False, 'shape': 'rect'},
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

# add a constant here and an elif in pikart_host.py:activate_consumable to register a new powerup
POWERUP_SIZE_GROW   = 'size_grow'
POWERUP_SIZE_SHRINK = 'size_shrink'
POWERUP_GREEN_SHELL = 'green_shell'
POWERUP_RED_SHELL   = 'red_shell'

CONSUMABLE_POOL = [POWERUP_SIZE_GROW, POWERUP_SIZE_SHRINK, POWERUP_GREEN_SHELL, POWERUP_RED_SHELL]

_POWERUP_LABEL = {
    POWERUP_SIZE_GROW:   'GROW',
    POWERUP_SIZE_SHRINK: 'SHRINK',
    POWERUP_GREEN_SHELL: 'GREEN',
    POWERUP_RED_SHELL:   'RED',
}

# =============================================================================
# Network helpers
# =============================================================================

DISCONNECTED = object()   # sentinel used to signal thread shutdown


def flush_queue(q: queue.Queue):
    while True:
        try: q.get_nowait()
        except queue.Empty: break


def send_msg(sock: socket.socket, payload: dict):
    data = json.dumps(payload).encode()
    sock.sendall(struct.pack('>I', len(data)) + data)


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


# drains send_q, sends each message; on exit or error puts DISCONNECTED into signal_q
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


# reads messages from socket into recv_q; on exit signals both signal queues
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
# Physics helpers (used internally by game objects)
# =============================================================================

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


def tri_mask_overlap(player_rect: pygame.Rect, tile_rect: pygame.Rect,
                     tile_type: str, tri_masks: dict,
                     player_mask: pygame.mask.Mask) -> bool:
    tri_mask = tri_masks.get(tile_type)
    if tri_mask is None:
        return False
    return tri_mask.overlap(player_mask, (player_rect.left - tile_rect.left,
                                          player_rect.top  - tile_rect.top)) is not None

def draw_track_history(patch: pygame.Surface, track_history: collections.deque,
                       color: tuple[int, int, int], plx: float, pty: float):
    """Draw track marks onto the camera patch surface in patch-space coordinates."""
    for wx, wy in track_history:
        pygame.draw.rect(patch, color, (int(wx - plx) - 1, int(wy - pty) - 1, 2, 2))


# =============================================================================
# Game objects
# =============================================================================

class Tile:
    def __init__(self, tile_type: str, grid_x: int, grid_y: int):
        self.tile_type  = tile_type
        self.world_rect = pygame.Rect(grid_x * TILE_SIZE, grid_y * TILE_SIZE, TILE_SIZE, TILE_SIZE)


class Map:
    def __init__(self, filepath: str):
        with open(filepath) as f:
            lines = f.read().splitlines()

        self.rows = len(lines)
        self.cols = max(len(l) for l in lines)
        self.pixel_width  = self.cols * TILE_SIZE
        self.pixel_height = self.rows * TILE_SIZE
        self.grid: list[list[Tile]] = []

        p1_spawn = p2_spawn = None
        finish_tiles: list[tuple[int, int]] = []
        self.spawner_positions: list[tuple[float, float]] = []
        tc = lambda c, r: (c * TILE_SIZE + TILE_SIZE / 2, r * TILE_SIZE + TILE_SIZE / 2)

        for r, line in enumerate(lines):
            row = []
            for c in range(self.cols):
                tt = CHAR_TO_TILE_TYPE.get(line[c] if c < len(line) else ' ', 'open')
                if   tt == 'player1_spawn': p1_spawn = tc(c, r)
                elif tt == 'player2_spawn': p2_spawn = tc(c, r)
                elif tt == 'finish_line':   finish_tiles.append((r, c))
                elif tt == 'spawner':       self.spawner_positions.append(tc(c, r))
                row.append(Tile(tt, c, r))
            self.grid.append(row)

        if p1_spawn is None: raise ValueError("Map missing '1' spawn.")
        if p2_spawn is None: raise ValueError("Map missing '2' spawn.")
        if len(finish_tiles) != 7:
            raise ValueError(f"Map needs 7 '|' finish tiles, found {len(finish_tiles)}.")
        finish_cols = {c for _, c in finish_tiles}
        if len(finish_cols) != 1:
            raise ValueError("Finish tiles must form a single vertical column.")
        finish_rows = sorted(r for r, _ in finish_tiles)
        if any(b != a+1 for a, b in zip(finish_rows, finish_rows[1:])):
            raise ValueError("Finish tiles must be contiguous.")

        self.player1_spawn = p1_spawn
        self.player2_spawn = p2_spawn
        fc = next(iter(finish_cols))
        self.finish_line_x     = fc * TILE_SIZE + TILE_SIZE / 2
        self.finish_line_y_min = finish_rows[0]  * TILE_SIZE
        self.finish_line_y_max = (finish_rows[-1] + 1) * TILE_SIZE

        # marching squares: infer diagonal tiles from wall corner pairs
        walls = [[t.tile_type == 'wall' for t in row] for row in self.grid]
        for r in range(self.rows - 1):
            for c in range(self.cols - 1):
                if walls[r][c] and walls[r+1][c+1]:
                    self.grid[r][c+1].tile_type   = 'tri_bottom_left'
                    self.grid[r+1][c].tile_type   = 'tri_top_right'
                if walls[r][c+1] and walls[r+1][c]:
                    self.grid[r][c].tile_type     = 'tri_bottom_right'
                    self.grid[r+1][c+1].tile_type = 'tri_top_left'

        # BFS from p1 spawn to classify interior vs exterior tiles
        sc = int(self.player1_spawn[0] // TILE_SIZE)
        sr = int(self.player1_spawn[1] // TILE_SIZE)
        self.interior: set[tuple[int, int]] = {(sr, sc)}
        stack = [(sr, sc)]
        while stack:
            r, c = stack.pop()
            for dr, dc in ((-1,0),(1,0),(0,-1),(0,1)):
                nr, nc = r+dr, c+dc
                if (nr, nc) in self.interior or not (0 <= nr < self.rows and 0 <= nc < self.cols):
                    continue
                tt = self.grid[nr][nc].tile_type
                if TILE_TYPE_INFO.get(tt, TILE_TYPE_INFO['open'])['is_wall'] and not tt.startswith('tri_'):
                    continue
                self.interior.add((nr, nc))
                stack.append((nr, nc))

    def is_interior(self, world_x: float, world_y: float) -> bool:
        return (int(world_y) // TILE_SIZE, int(world_x) // TILE_SIZE) in self.interior

    # BFS outward from (world_x, world_y) to find center of nearest interior tile
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
                for dr, dc in ((-1,0),(1,0),(0,-1),(0,1)):
                    nr, nc = r+dr, c+dc
                    if (nr, nc) in visited or not (0 <= nr < self.rows and 0 <= nc < self.cols):
                        continue
                    visited.add((nr, nc))
                    if (nr, nc) in self.interior:
                        return (nc * TILE_SIZE + TILE_SIZE / 2, nr * TILE_SIZE + TILE_SIZE / 2)
                    next_q.append((nr, nc))
            q = next_q
        return self.player1_spawn

    def get_tiles_in_rect(self, world_rect: pygame.Rect) -> list[Tile]:
        c0 = max(0, world_rect.left   // TILE_SIZE)
        c1 = min(self.cols, world_rect.right  // TILE_SIZE + 1)
        r0 = max(0, world_rect.top    // TILE_SIZE)
        r1 = min(self.rows, world_rect.bottom // TILE_SIZE + 1)
        return [self.grid[r][c] for r in range(r0, r1) for c in range(c0, c1)]

    def build_surface(self) -> pygame.Surface:
        surf = pygame.Surface((self.pixel_width, self.pixel_height), pygame.SRCALPHA)
        for row in self.grid:
            for tile in row:
                info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                wr   = tile.world_rect
                is_interior = (wr.top // TILE_SIZE, wr.left // TILE_SIZE) in self.interior
                if info['shape'] == 'rect':
                    pygame.draw.rect(surf, info['color'] if info['is_wall'] or is_interior else COLOR_BACKGROUND, wr)
                elif info['shape'] == 'arrow':
                    pygame.draw.rect(surf, COLOR_OPEN if is_interior else COLOR_BACKGROUND, wr)
                    if is_interior:
                        sprite = _load_arrow_sprite(info['direction'])
                        surf.blit(sprite, sprite.get_rect(center=wr.center))
                else:
                    pygame.draw.rect(surf, COLOR_OPEN if is_interior else COLOR_BACKGROUND, wr)
                    m = TILE_SIZE - 1
                    pts = [(int(wr.x + px*m), int(wr.y + py*m)) for px, py in info['points']]
                    pygame.draw.polygon(surf, info['color'], pts)
        return surf


class Camera:
    def __init__(self, viewport_w: int, viewport_h: int):
        self.viewport_w = viewport_w
        self.viewport_h = viewport_h
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self.heading:  float = 0.0

    def center_on(self, world_x: float, world_y: float):
        self.offset_x = world_x - self.viewport_w / 2
        self.offset_y = world_y - self.viewport_h / 2


# green shell travels backward in a straight line; red shell homes toward target
class Shell:
    def __init__(self, shell_type: str, world_x: float, world_y: float, heading: float, owner_index: int):
        self.shell_type        = shell_type
        self.world_x           = world_x
        self.world_y           = world_y
        self.heading           = heading
        self.vel_x             = math.cos(heading) * SHELL_SPEED
        self.vel_y             = math.sin(heading) * SHELL_SPEED
        self.owner_index       = owner_index
        self.bounces_remaining = 3

    def get_bounding_rect(self) -> pygame.Rect:
        h = SHELL_SIZE // 2
        return pygame.Rect(int(self.world_x) - h, int(self.world_y) - h, SHELL_SIZE, SHELL_SIZE)

    def update(self, dt: float, target_x: float, target_y: float):
        if self.shell_type == POWERUP_RED_SHELL:
            dx, dy = target_x - self.world_x, target_y - self.world_y
            if dx or dy:
                diff = (math.atan2(dy, dx) - self.heading + math.pi) % (2*math.pi) - math.pi
                self.heading += clamp(diff, -RED_SHELL_TURN * dt, RED_SHELL_TURN * dt)
                self.vel_x = math.cos(self.heading) * SHELL_SPEED
                self.vel_y = math.sin(self.heading) * SHELL_SPEED
        self.world_x += self.vel_x * dt
        self.world_y += self.vel_y * dt

    # returns (nx, ny) wall normal if colliding, else None
    def hits_wall(self, game_map: 'Map', tri_masks: dict, player_mask: pygame.mask.Mask) -> tuple[float, float] | None:
        rect = self.get_bounding_rect()
        for tile in game_map.get_tiles_in_rect(rect):
            info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
            if not info['is_wall']:
                continue
            if info['shape'] == 'tri' and not tri_mask_overlap(rect, tile.world_rect, tile.tile_type, tri_masks, player_mask):
                continue
            contact = aabb_mtv(rect, tile.world_rect)
            return (contact[0], contact[1]) if contact else (1.0, 0.0)
        return None

    def hits_vehicle(self, vehicle: 'Vehicle') -> bool:
        return self.get_bounding_rect().colliderect(vehicle.get_bounding_rect())

    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        color = COLOR_SHELL_GREEN if self.shell_type == POWERUP_GREEN_SHELL else COLOR_SHELL_RED
        h = SHELL_SIZE // 2
        rect = pygame.Rect(screen_x - h, screen_y - h, SHELL_SIZE, SHELL_SIZE)
        pygame.draw.rect(surface, color, rect)
        pygame.draw.rect(surface, (0, 0, 0), rect, 1)

    def to_dict(self) -> dict:
        return {'type': self.shell_type, 'x': round(self.world_x, 3), 'y': round(self.world_y, 3)}

    @staticmethod
    def from_dict(data: dict) -> 'Shell':
        return Shell(data['type'], float(data['x']), float(data['y']), 0.0, 0)


class PowerupSpawner:
    def __init__(self, world_x: float, world_y: float):
        self.world_x           = world_x
        self.world_y           = world_y
        self.available_powerup: str | None = random.choice(CONSUMABLE_POOL)
        self.cooldown_timer:   float = 0.0

    def get_bounding_rect(self) -> pygame.Rect:
        h = TILE_SIZE // 2
        return pygame.Rect(int(self.world_x) - h, int(self.world_y) - h, TILE_SIZE, TILE_SIZE)

    def update(self, dt: float):
        if self.available_powerup is None:
            self.cooldown_timer = max(0.0, self.cooldown_timer - dt)
            if self.cooldown_timer == 0.0:
                self.available_powerup = random.choice(CONSUMABLE_POOL)

    # transfers powerup to vehicle if eligible; returns True on success
    def try_collect(self, vehicle: 'Vehicle') -> bool:
        if self.available_powerup is None or vehicle.stored_powerup is not None:
            return False
        if not self.get_bounding_rect().colliderect(vehicle.get_bounding_rect()):
            return False
        vehicle.stored_powerup = self.available_powerup
        self.available_powerup = None
        self.cooldown_timer    = SPAWNER_RESPAWN_COOLDOWN
        return True

    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        h = TILE_SIZE // 2
        rect = pygame.Rect(screen_x - h, screen_y - h, TILE_SIZE, TILE_SIZE)
        pygame.draw.rect(surface, COLOR_SPAWNER if self.available_powerup else COLOR_SPAWNER_EMPTY, rect)
        pygame.draw.rect(surface, (0, 0, 0), rect, 1)

    def to_dict(self) -> dict:
        return {'x': round(self.world_x, 3), 'y': round(self.world_y, 3),
                'occupied': self.available_powerup is not None}

    @staticmethod
    def from_dict(data: dict) -> 'PowerupSpawner':
        sp = PowerupSpawner(float(data['x']), float(data['y']))
        sp.available_powerup = 'occupied' if data.get('occupied') else None
        return sp


class Vehicle:
    def __init__(self, start_x: float, start_y: float,
                 throttle_forward_key: int, throttle_back_key: int,
                 steer_left_key: int, steer_right_key: int,
                 color: tuple[int, int, int]):
        self.world_x    = start_x
        self.world_y    = start_y
        self.vel_x: float = 0.0
        self.vel_y: float = 0.0
        self.throttle_input: float = 0.0
        self.steer_input:    float = 0.0
        self.heading:   float = 0.0
        self.ang_vel:   float = 0.0
        self.prev_world_x = start_x
        self.prev_world_y = start_y
        self.curr_lap:  int = 1
        self.max_lap:   int = 1
        self.race_finished = False
        self.finish_place: int | None = None
        self.ignore_first_forward_cross = True
        self.throttle_forward_key = throttle_forward_key
        self.throttle_back_key    = throttle_back_key
        self.steer_left_key       = steer_left_key
        self.steer_right_key      = steer_right_key
        self.color                = color
        self.stored_powerup: str | None = None
        self.size_multiplier: float = 1.0
        self.size_timer:      float = 0.0
        self.on_powerup1 = False
        self.on_powerup2 = False
        self.track_history: collections.deque = collections.deque(maxlen=TRACK_HISTORY_MAX)
        self.track_frame_counter: int = 0

    def get_bounding_rect(self) -> pygame.Rect:
        size = int(PLAYER_SIZE * self.size_multiplier)
        h = size // 2
        return pygame.Rect(int(self.world_x) - h, int(self.world_y) - h, size, size)

    def handle_input(self):
        keys = pygame.key.get_pressed()
        self.throttle_input = float(int(keys[self.throttle_forward_key]) - int(keys[self.throttle_back_key]))
        self.steer_input    = float(int(keys[self.steer_right_key])      - int(keys[self.steer_left_key]))

    def update_physics(self, dt: float):
        self.prev_world_x, self.prev_world_y = self.world_x, self.world_y
        if self.size_timer > 0.0:
            self.size_timer = max(0.0, self.size_timer - dt)
            if self.size_timer <= 0.0:
                self.size_multiplier = 1.0
        ang_acc      = PLAYER_ANG_ACCEL * self.steer_input - PLAYER_ANG_DAMP * self.ang_vel
        self.ang_vel = clamp(self.ang_vel + ang_acc * dt, -PLAYER_MAX_ANG_VEL, PLAYER_MAX_ANG_VEL)
        self.heading = (self.heading + self.ang_vel * dt) % (2.0 * math.pi)
        fwd          = PLAYER_ACCEL * self.throttle_input
        self.vel_x   = clamp(self.vel_x + (fwd * math.cos(self.heading) - PLAYER_DRAG * self.vel_x) * dt,
                             -PLAYER_MAX_SPEED_SAFETY, PLAYER_MAX_SPEED_SAFETY)
        self.vel_y   = clamp(self.vel_y + (fwd * math.sin(self.heading) - PLAYER_DRAG * self.vel_y) * dt,
                             -PLAYER_MAX_SPEED_SAFETY, PLAYER_MAX_SPEED_SAFETY)
        self.world_x += self.vel_x * dt
        self.world_y += self.vel_y * dt

    def resolve_collisions(self, game_map: 'Map', tri_masks: dict, player_mask: pygame.mask.Mask) -> bool:
        """Resolve wall collisions via iterative MTV correction.

        Returns True if at least one collision was corrected this call.
        """
        any_corrected = False
        for _ in range(MAX_COLLISION_PASSES):
            corrected = False
            for tile in game_map.get_tiles_in_rect(self.get_bounding_rect()):
                info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                if not info['is_wall']:
                    continue
                rect = self.get_bounding_rect()
                if info['shape'] == 'tri' and not tri_mask_overlap(rect, tile.world_rect, tile.tile_type, tri_masks, player_mask):
                    continue
                contact = aabb_mtv(rect, tile.world_rect)
                if contact is None:
                    continue
                nx, ny, pen = contact
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
        if abs(self.vel_x) < 1e-3: self.vel_x = 0.0
        if abs(self.vel_y) < 1e-3: self.vel_y = 0.0
        return any_corrected

    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        _draw_player_sprite(surface, screen_x, screen_y, self.color, self.size_multiplier,
                            self.heading)


# =============================================================================
# Render helpers (used by both host and client)
# =============================================================================

def make_button_rect(center_x: int, center_y: int, width: int = 110, height: int = 40) -> pygame.Rect:
    r = pygame.Rect(0, 0, width, height)
    r.center = (center_x, center_y)
    return r


def _btn(screen: pygame.Surface, rect: pygame.Rect, label: str, font: pygame.font.Font,
         selected: bool = False,
         bg=(210,210,210), bg_h=(235,235,235), fg=(0,0,0), border=(0,0,0), bw: int = 3):
    pygame.draw.rect(screen, bg_h if selected else bg, rect)
    pygame.draw.rect(screen, border, rect, bw)
    s = font.render(label, True, fg)
    screen.blit(s, s.get_rect(center=rect.center))


def _blit_c(screen: pygame.Surface, surf: pygame.Surface, center):
    screen.blit(surf, surf.get_rect(center=center))


def _text_box(screen: pygame.Surface, surf: pygame.Surface, box: pygame.Rect, border: int = 2):
    pygame.draw.rect(screen, (255, 255, 255), box)
    pygame.draw.rect(screen, (0, 0, 0), box, border)
    screen.blit(surf, surf.get_rect(center=box.center))


def render_status_screen(screen: pygame.Surface, title_font: pygame.font.Font,
                         button_font: pygame.font.Font, message: str):
    screen.fill((25, 25, 25))
    _blit_c(screen, title_font.render('PiKart', True, (255,255,255)), (VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//5))
    _blit_c(screen, button_font.render(message, True, (180,180,180)), (VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2))


def render_menu(screen: pygame.Surface, play_rect: pygame.Rect, quit_rect: pygame.Rect,
                title_font: pygame.font.Font, button_font: pygame.font.Font,
                error_msg: str | None, selected_idx: int = 0):
    screen.fill((25, 25, 25))
    _blit_c(screen, title_font.render('PiKart', True, (255,255,255)), (VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//5))
    if error_msg:
        _blit_c(screen, button_font.render(error_msg, True, COLOR_ERROR), (VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2 - 55))
    _btn(screen, play_rect, 'Play', button_font, selected_idx == 0,
         bg=(50,50,80), bg_h=(80,80,130), fg=(255,255,255), border=(120,120,180))
    _btn(screen, quit_rect, 'Quit', button_font, selected_idx == 1,
         bg=(50,50,80), bg_h=(80,80,130), fg=(255,255,255), border=(120,120,180))


def render_post_race(screen: pygame.Surface, play_again_rect: pygame.Rect, menu_rect: pygame.Rect,
                     title_font: pygame.font.Font, button_font: pygame.font.Font,
                     selected_idx: int = 0, show_play_again: bool = True):
    overlay = pygame.Surface((VIEWPORT_WIDTH, VIEWPORT_HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    screen.blit(overlay, (0, 0))
    _blit_c(screen, title_font.render('Race Over!', True, (255,255,255)), (VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2 - 80))
    if show_play_again:
        _btn(screen, play_again_rect, 'Play Again', button_font, selected_idx == 0,
             bg=(50,50,80), bg_h=(80,80,130), fg=(255,255,255), border=(120,120,180))
    _btn(screen, menu_rect, 'Exit to Menu', button_font, selected_idx == (1 if show_play_again else 0),
         bg=(50,50,80), bg_h=(80,80,130), fg=(255,255,255), border=(120,120,180))


def build_minimap_surface(map_surface: pygame.Surface, game_map: 'Map') -> pygame.Surface:
    """Scale map_surface down to fit within MINIMAP_W x MINIMAP_H, preserving aspect ratio."""
    scale = min(MINIMAP_W / game_map.pixel_width, MINIMAP_H / game_map.pixel_height)
    w = max(1, int(game_map.pixel_width  * scale))
    h = max(1, int(game_map.pixel_height * scale))
    base = pygame.Surface((w, h))
    base.fill(COLOR_BACKGROUND)
    scaled = pygame.transform.scale(map_surface, (w, h))
    base.blit(scaled, (0, 0))
    return base


def render_minimap(screen: pygame.Surface, minimap_surface: pygame.Surface,
                   vehicles: list, game_map: 'Map', viewport_rect: pygame.Rect):
    """Draw the mini-map with a 1px border and rotated vehicle sprites in the bottom-right corner."""
    mw = minimap_surface.get_width()
    mh = minimap_surface.get_height()
    scale_x = mw / game_map.pixel_width
    scale_y = mh / game_map.pixel_height
    x = viewport_rect.right  - MINIMAP_MARGIN - mw
    y = viewport_rect.bottom - MINIMAP_MARGIN - mh
    pygame.draw.rect(screen, (160, 160, 160), (x - 1, y - 1, mw + 2, mh + 2))
    screen.blit(minimap_surface, (x, y))
    for v in vehicles:
        dx = int(v.world_x * scale_x)
        dy = int(v.world_y * scale_y)
        filename = 'red_car.png' if v.color == COLOR_PLAYER else 'blue_car.png'
        sprite = _load_player_sprite(filename)
        sprite = pygame.transform.scale(sprite, (8, 8)).convert_alpha()
        angle  = -math.degrees(v.heading) - 90.0
        sprite = pygame.transform.rotate(sprite, angle)
        screen.blit(sprite, sprite.get_rect(center=(x + dx, y + dy)))


def render_center_overlay_message(screen: pygame.Surface, message: str, font: pygame.font.Font):
    surf = font.render(message, True, (0, 0, 0))
    px, py = 12, 6
    box = pygame.Rect(VIEWPORT_WIDTH//2  - (surf.get_width()  + 2*px)//2,
                      VIEWPORT_HEIGHT//2 - (surf.get_height() + 2*py)//2,
                      surf.get_width() + 2*px, surf.get_height() + 2*py)
    _text_box(screen, surf, box, border=3)


def render_map(screen: pygame.Surface, map_surface: pygame.Surface, camera: Camera,
               viewport_rect: pygame.Rect | None = None,
               overlay_vehicles: list | None = None,
               overlay_shells: list | None = None,
               overlay_spawners: list | None = None,
               track_histories: list[tuple[collections.deque, tuple]] | None = None):
    if viewport_rect is None:
        viewport_rect = pygame.Rect(0, 0, camera.viewport_w, camera.viewport_h)
    diag = int(math.ceil(math.sqrt(camera.viewport_w**2 + camera.viewport_h**2))) + 2*TILE_SIZE
    patch = pygame.Surface((diag, diag), pygame.SRCALPHA)
    cx, cy = camera.offset_x + camera.viewport_w/2.0, camera.offset_y + camera.viewport_h/2.0
    plx, pty = cx - diag/2.0, cy - diag/2.0
    patch.blit(map_surface, (-int(round(plx)), -int(round(pty))))
    if track_histories:
        for history, color in track_histories:
            draw_track_history(patch, history, color, plx, pty)
    for group in (overlay_spawners, overlay_vehicles, overlay_shells):
        if group:
            for obj in group:
                obj.draw(patch, int(round(obj.world_x - plx)), int(round(obj.world_y - pty)))
    rotated = pygame.transform.rotate(patch, math.degrees(camera.heading) + 90.0)
    screen.blit(rotated, rotated.get_rect(center=viewport_rect.center))


def render_hud(screen: pygame.Surface, viewport_rect: pygame.Rect,
               max_lap: int, total_laps: int, total_timer: float, lap_timer: float,
               finish_place: int | None, hud_font: pygame.font.Font,
               place_font: pygame.font.Font, stored_powerup: str | None = None):
    w, h, m = 46, 22, 5
    lap_box = pygame.Rect(viewport_rect.right - m - w, viewport_rect.top + m, w, h)
    _text_box(screen, hud_font.render(f"{min(max_lap, total_laps)}/{total_laps}", True, (0,0,0)), lap_box)

    def fmt(t): return f"{int(t//60)}:{t%60:05.2f}"
    ts = hud_font.render(f"T: {fmt(total_timer)}", True, (0,140,0))
    ls = hud_font.render(f"L: {fmt(lap_timer)}",   True, (0,140,0))
    cw, px, py = max(ts.get_width(), ls.get_width()), 5, 3
    box = pygame.Rect(viewport_rect.centerx - (cw + 2*px)//2, viewport_rect.top + m,
                      cw + 2*px, ts.get_height() + ls.get_height() + 2 + 2*py)
    pygame.draw.rect(screen, (255,255,255), box)
    pygame.draw.rect(screen, (0,0,0), box, 1)
    screen.blit(ts, ts.get_rect(centerx=box.centerx, top=box.top + py))
    screen.blit(ls, ls.get_rect(centerx=box.centerx, top=box.top + py + ts.get_height() + 2))

    if finish_place is not None:
        p   = finish_place
        suf = 'th' if 10 <= p % 100 <= 20 else {1:'st',2:'nd',3:'rd'}.get(p%10,'th')
        s   = place_font.render(f"{p}{suf}", True, (0,0,0))
        _text_box(screen, s, pygame.Rect(viewport_rect.centerx - (s.get_width()+14)//2,
                                         viewport_rect.centery - 20,
                                         s.get_width()+14, s.get_height()+10))

    label = _POWERUP_LABEL.get(stored_powerup, 'NONE') if stored_powerup else 'NONE'
    s = hud_font.render(label, True, (0,0,0))
    pw = max(s.get_width() + 6, w)
    powerup_box = pygame.Rect(viewport_rect.right - m - pw, lap_box.bottom + 3, pw, h)
    _text_box(screen, s, powerup_box)

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

_motor_pi:       object = None   # pigpio.pi instance
_motor_end_time: float  = 0.0    # monotonic timestamp when current pulse ends


def motor_init():
    """Connect pigpio and configure the motor GPIO pin as output."""
    global _motor_pi
    import pigpio as _pg
    _motor_pi = _pg.pi()
    if not _motor_pi.connected:
        raise RuntimeError("pigpiod not running — start with: sudo pigpiod")
    _motor_pi.set_mode(MOTOR_GPIO, _pg.OUTPUT)
    _motor_pi.write(MOTOR_GPIO, 0)


def motor_stop():
    """Turn off the motor and disconnect pigpio."""
    global _motor_pi
    if _motor_pi is not None:
        _motor_pi.write(MOTOR_GPIO, 0)
        _motor_pi.stop()
        _motor_pi = None


def motor_rumble():
    """Trigger a single fixed-duration rumble pulse.

    Safe to call while a pulse is already running; the end time is only
    extended if the new deadline is later than the current one.
    """
    import time as _t
    global _motor_end_time
    deadline = _t.monotonic() + RUMBLE_DURATION
    if deadline > _motor_end_time:
        _motor_end_time = deadline
    if _motor_pi is not None:
        _motor_pi.write(MOTOR_GPIO, 1)


def motor_update():
    """Turn the motor off once the pulse duration has elapsed.

    Must be called once per frame from the main loop.
    """
    import time as _t
    global _motor_end_time
    if _motor_pi is not None and _motor_end_time > 0.0 and _t.monotonic() >= _motor_end_time:
        _motor_pi.write(MOTOR_GPIO, 0)
        _motor_end_time = 0.0


# =============================================================================
# Joystick input (MCP3008 via bit-banged SPI, pigpio)
#
# Wiring:
#   MCP3008 CLK  -> GPIO 5    MCP3008 MOSI -> GPIO 6
#   MCP3008 MISO -> GPIO 13   MCP3008 CS   -> GPIO 19
#   Joystick VRx -> CH0 (steer)   VRy -> CH1 (throttle)
#   P1 button -> GPIO 27 (active low)   P2 button -> GPIO 17 (active low)
# =============================================================================

_JS_CLK  = 5
_JS_MOSI = 6
_JS_MISO = 13
_JS_CS   = 19
JS_SW    = 27   # canonical joystick switch GPIO (matches joystick_test wiring)

_JS_SPI_DELAY    = 0.00001   # 10 µs
_JS_POLL_HZ      = 100
_JS_DEADZONE     = 8         # raw units, same as test script
_JS_CENTRE       = 256       # centre of 0-512 raw range
_JS_MENU_TRIGGER = 80        # raw units from centre to trigger a menu move
_JS_MENU_NEUTRAL = 30        # raw units from centre to re-arm

# module-level state — stored as raw ADC values (0-512)
_js_pi       = None
_js_lock     = threading.Lock()
_js_x_raw    = _JS_CENTRE
_js_y_raw    = _JS_CENTRE
_js_menu_x_armed = True
_js_menu_y_armed = True
_js_running  = False
_js_prev_pressed: bool = False
_js_press_latched: bool = False


def _js_read_mcp3008(channel: int) -> int:
    pi = _js_pi
    pi.write(_JS_CS, 0)
    import time as _t; _t.sleep(_JS_SPI_DELAY)
    for bit in [1, 1, (channel >> 2) & 1, (channel >> 1) & 1, channel & 1]:
        pi.write(_JS_MOSI, bit)
        _t.sleep(_JS_SPI_DELAY)
        pi.write(_JS_CLK, 1); _t.sleep(_JS_SPI_DELAY)
        pi.write(_JS_CLK, 0); _t.sleep(_JS_SPI_DELAY)
    pi.write(_JS_CLK, 1); _t.sleep(_JS_SPI_DELAY)
    pi.write(_JS_CLK, 0); _t.sleep(_JS_SPI_DELAY)
    result = 0
    for _ in range(10):
        pi.write(_JS_CLK, 1); _t.sleep(_JS_SPI_DELAY)
        result = (result << 1) | pi.read(_JS_MISO)
        pi.write(_JS_CLK, 0); _t.sleep(_JS_SPI_DELAY)
    pi.write(_JS_CS, 1)
    return result


# converts raw value to [-1.0, 1.0] with deadzone applied in raw units
def _js_to_float(raw: int) -> float:
    v = raw - _JS_CENTRE
    if abs(v) < _JS_DEADZONE:
        return 0.0
    return max(-1.0, min(1.0, v / _JS_CENTRE))


def _js_poll_loop():
    import time as _t
    interval = 1.0 / _JS_POLL_HZ
    global _js_x_raw, _js_y_raw
    while _js_running:
        x = _js_read_mcp3008(0)
        y = _js_read_mcp3008(1)
        pressed = False
        try:
            pressed = (_js_pi.read(JS_SW) == 0)
        except Exception:
            pressed = False
        with _js_lock:
            # detect rising edge and latch it
            global _js_prev_pressed, _js_press_latched
            if pressed and not _js_prev_pressed:
                _js_press_latched = True
            _js_prev_pressed = pressed
            _js_x_raw = x
            _js_y_raw = y
        _t.sleep(interval)


def joystick_init():
    global _js_pi, _js_running
    import pigpio
    _js_pi = pigpio.pi()
    if not _js_pi.connected:
        raise RuntimeError("pigpiod not running — start with: sudo pigpiod")
    import pigpio as _pg
    for pin, mode in ((_JS_CLK, _pg.OUTPUT), (_JS_MOSI, _pg.OUTPUT),
                      (_JS_MISO, _pg.INPUT), (_JS_CS, _pg.OUTPUT),
                      (JS_SW, _pg.INPUT)):
        _js_pi.set_mode(pin, mode)
    _js_pi.set_pull_up_down(JS_SW, _pg.PUD_UP)
    _js_pi.write(_JS_CS, 1)
    _js_pi.write(_JS_CLK, 0)
    _js_running = True
    threading.Thread(target=_js_poll_loop, daemon=True).start()


def joystick_stop():
    global _js_running
    _js_running = False
    if _js_pi:
        _js_pi.stop()


def joystick_throttle() -> float:
    with _js_lock:
        return -_js_to_float(_js_y_raw)


def joystick_steer() -> float:
    with _js_lock:
        return _js_to_float(_js_x_raw)


# reads button state directly from GPIO, same as the test script — bypasses poll thread
def joystick_btn(sw_pin: int) -> bool:
    if _js_pi is None:
        return False
    return _js_pi.read(sw_pin) == 0   # active low


def joystick_consume_press() -> bool:
    """Atomically consume a latched joystick press (rising edge).

    Returns True once per physical press. Safe to call from host/client main loops.
    """
    global _js_press_latched
    with _js_lock:
        if _js_press_latched:
            _js_press_latched = False
            return True
        return False


def joystick_clear_latch() -> None:
    """Clear any latched press without consuming it (useful on state transitions)."""
    global _js_press_latched
    with _js_lock:
        _js_press_latched = False


# returns -1, 0, or +1 for a menu move on Y; re-arms after returning to neutral
def joystick_menu_y() -> int:
    global _js_menu_y_armed
    with _js_lock:
        y = _js_y_raw
    if _js_menu_y_armed:
        if y < _JS_CENTRE - _JS_MENU_TRIGGER:
            _js_menu_y_armed = False; return -1
        if y > _JS_CENTRE + _JS_MENU_TRIGGER:
            _js_menu_y_armed = False; return 1
    elif abs(y - _JS_CENTRE) < _JS_MENU_NEUTRAL:
        _js_menu_y_armed = True
    return 0


# returns -1, 0, or +1 for a menu move on X
def joystick_menu_x() -> int:
    global _js_menu_x_armed
    with _js_lock:
        x = _js_x_raw
    if _js_menu_x_armed:
        if x < _JS_CENTRE - _JS_MENU_TRIGGER:
            _js_menu_x_armed = False; return -1
        if x > _JS_CENTRE + _JS_MENU_TRIGGER:
            _js_menu_x_armed = False; return 1
    elif abs(x - _JS_CENTRE) < _JS_MENU_NEUTRAL:
        _js_menu_x_armed = True
    return 0