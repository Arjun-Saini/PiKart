import json
import math
import os
import socket
import struct

import pygame

# =============================================================================
# Network constants
# =============================================================================

HOST_IP = '127.0.0.1'
HOST_PORT = 5000

# =============================================================================
# Display constants
# =============================================================================

VIEWPORT_WIDTH = 800
VIEWPORT_HEIGHT = 600
FPS = 60

# =============================================================================
# Game state identifiers
# =============================================================================

STATE_MENU        = 'menu'
STATE_WAITING     = 'waiting'
STATE_MAP_SELECT  = 'map_select'
STATE_GAME        = 'game'
STATE_POST_RACE   = 'post_race'

# =============================================================================
# Race constants
# =============================================================================

TOTAL_LAPS             = 3
COUNTDOWN_DURATION     = 3.0
GO_DISPLAY_DURATION    = 0.75
MAX_PHYSICS_DT         = 1.0 / 30.0

# =============================================================================
# Map constants
# =============================================================================

TILE_SIZE = 10

# =============================================================================
# Vehicle constants
# =============================================================================

PLAYER_SIZE            = 10
PLAYER_ACCEL           = 900.0
PLAYER_DRAG            = 2.0
PLAYER_MAX_SPEED_SAFETY = 500.0
PLAYER_RESTITUTION     = 0.3
COLLISION_SLOP         = 0.05
MAX_COLLISION_PASSES   = 2
PLAYER_ANG_ACCEL       = 10.0
PLAYER_ANG_DAMP        = 5.0
PLAYER_MAX_ANG_VEL     = 4.5

# =============================================================================
# Colors
# =============================================================================

COLOR_BACKGROUND = ( 30,  30,  30)
COLOR_OPEN       = (255, 255, 255)
COLOR_WALL       = (  0,   0,   0)
COLOR_TRIANGLE   = (  0,   0,   0)
COLOR_POWERUP1   = (255, 220,   0)
COLOR_POWERUP2   = (120, 220, 255)
COLOR_PLAYER     = (220,  30,  30)
COLOR_PLAYER2    = ( 30, 100, 220)
COLOR_SPAWN      = ( 50, 100,  50)
COLOR_FINISH     = (180, 180, 180)
COLOR_ERROR      = (220,  60,  60)

# =============================================================================
# Tile type registry
# =============================================================================

TILE_TYPE_INFO = {
    'open': {
        'color': COLOR_OPEN,
        'is_wall': False,
        'shape': 'rect',
    },
    'wall': {
        'color': COLOR_WALL,
        'is_wall': True,
        'shape': 'rect',
    },
    'tri_top_left': {
        'color': COLOR_TRIANGLE,
        'is_wall': True,
        'shape': 'tri',
        'points': ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
    },
    'tri_top_right': {
        'color': COLOR_TRIANGLE,
        'is_wall': True,
        'shape': 'tri',
        'points': ((1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),
    },
    'tri_bottom_left': {
        'color': COLOR_TRIANGLE,
        'is_wall': True,
        'shape': 'tri',
        'points': ((0.0, 1.0), (1.0, 1.0), (0.0, 0.0)),
    },
    'tri_bottom_right': {
        'color': COLOR_TRIANGLE,
        'is_wall': True,
        'shape': 'tri',
        'points': ((1.0, 1.0), (1.0, 0.0), (0.0, 1.0)),
    },
    'powerup1': {
        'color': COLOR_POWERUP1,
        'is_wall': False,
        'shape': 'rect',
    },
    'powerup2': {
        'color': COLOR_POWERUP2,
        'is_wall': False,
        'shape': 'rect',
    },
    'player1_spawn': {
        'color': COLOR_SPAWN,
        'is_wall': False,
        'shape': 'rect',
    },
    'player2_spawn': {
        'color': COLOR_SPAWN,
        'is_wall': False,
        'shape': 'rect',
    },
    'finish_line': {
        'color': COLOR_FINISH,
        'is_wall': False,
        'shape': 'rect',
    },
}

CHAR_TO_TILE_TYPE = {
    '#': 'wall',
    '*': 'powerup1',
    '+': 'powerup2',
    '1': 'player1_spawn',
    '2': 'player2_spawn',
    '|': 'finish_line',
}


# =============================================================================
# Network helpers
# =============================================================================

def send_msg(sock: socket.socket, payload: dict):
    """Sends a length-prefixed JSON message on sock. Raises OSError on failure."""
    data = json.dumps(payload).encode('utf-8')
    header = struct.pack('>I', len(data))
    sock.sendall(header + data)


def recv_msg(sock: socket.socket) -> dict:
    """Reads one length-prefixed JSON message from sock. Raises OSError on failure."""
    header = recv_exactly(sock, 4)
    length = struct.unpack('>I', header)[0]
    data = recv_exactly(sock, length)
    return json.loads(data.decode('utf-8'))


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Reads exactly n bytes from sock, raising OSError if the connection closes."""
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError('Connection closed.')
        buf += chunk
    return buf


# =============================================================================
# Map tile mask setup (called after pygame.init())
# =============================================================================

def build_triangle_mask(normalized_points: tuple[tuple[float, float], ...]) -> pygame.mask.Mask:
    surface = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
    max_idx = TILE_SIZE - 1
    pixel_points = [
        (int(px * max_idx), int(py * max_idx))
        for px, py in normalized_points
    ]
    pygame.draw.polygon(surface, (255, 255, 255), pixel_points)
    return pygame.mask.from_surface(surface)


def build_player_rect_mask() -> pygame.mask.Mask:
    surface = pygame.Surface((PLAYER_SIZE, PLAYER_SIZE), pygame.SRCALPHA)
    pygame.draw.rect(surface, (255, 255, 255), surface.get_rect())
    return pygame.mask.from_surface(surface)


def build_masks() -> tuple[dict, pygame.mask.Mask]:
    """Builds and returns (TRIANGLE_MASKS, PLAYER_COLLISION_MASK). Call after pygame.init()."""
    triangle_masks = {
        tile_type: build_triangle_mask(tile_info['points'])
        for tile_type, tile_info in TILE_TYPE_INFO.items()
        if tile_info.get('shape') == 'tri'
    }
    player_mask = build_player_rect_mask()
    return triangle_masks, player_mask


# =============================================================================
# Game objects
# =============================================================================

# represents one map tile; stores its type and world-space rect
class Tile:
    def __init__(self, tile_type: str, grid_x: int, grid_y: int):
        self.tile_type: str = tile_type
        self.world_rect: pygame.Rect = pygame.Rect(
            grid_x * TILE_SIZE, grid_y * TILE_SIZE, TILE_SIZE, TILE_SIZE
        )


# converts a map file into a tile matrix
class Map:
    def __init__(self, filepath: str):
        self.grid: list[list] = []
        self.rows: int = 0
        self.cols: int = 0
        self.pixel_width: int = 0
        self.pixel_height: int = 0
        self.player1_spawn: tuple[float, float] = (0.0, 0.0)
        self.player2_spawn: tuple[float, float] = (0.0, 0.0)
        self.finish_line_x: float = 0.0
        self.finish_line_y_min: float = 0.0
        self.finish_line_y_max: float = 0.0

        with open(filepath, 'r') as f:
            lines = f.read().splitlines()

        self.rows = len(lines)
        self.cols = max(len(line) for line in lines)
        self.pixel_width = self.cols * TILE_SIZE
        self.pixel_height = self.rows * TILE_SIZE

        player1_spawn: tuple[float, float] | None = None
        player2_spawn: tuple[float, float] | None = None
        finish_tiles: list[tuple[int, int]] = []

        for row_idx, line in enumerate(lines):
            row = []
            for col_idx in range(self.cols):
                tile_char = line[col_idx] if col_idx < len(line) else ' '
                tile_type = CHAR_TO_TILE_TYPE.get(tile_char, 'open')

                if tile_type == 'player1_spawn':
                    if player1_spawn is not None:
                        raise ValueError("Map must contain exactly one '1' spawn marker.")
                    player1_spawn = (
                        col_idx * TILE_SIZE + TILE_SIZE / 2,
                        row_idx * TILE_SIZE + TILE_SIZE / 2,
                    )
                elif tile_type == 'player2_spawn':
                    if player2_spawn is not None:
                        raise ValueError("Map must contain exactly one '2' spawn marker.")
                    player2_spawn = (
                        col_idx * TILE_SIZE + TILE_SIZE / 2,
                        row_idx * TILE_SIZE + TILE_SIZE / 2,
                    )
                elif tile_type == 'finish_line':
                    finish_tiles.append((row_idx, col_idx))

                row.append(Tile(tile_type=tile_type, grid_x=col_idx, grid_y=row_idx))
            self.grid.append(row)

        if player1_spawn is None:
            raise ValueError("Map must contain a '1' spawn marker.")
        if player2_spawn is None:
            raise ValueError("Map must contain a '2' spawn marker.")
        if len(finish_tiles) != 7:
            raise ValueError(
                f"Map must contain exactly seven '|' finish markers, found {len(finish_tiles)}."
            )

        finish_cols = {col for _, col in finish_tiles}
        if len(finish_cols) != 1:
            raise ValueError("Finish markers must form a single vertical line.")

        finish_rows = sorted(row for row, _ in finish_tiles)
        if any(curr != prev + 1 for prev, curr in zip(finish_rows, finish_rows[1:])):
            raise ValueError("Finish markers must be contiguous vertically.")

        finish_col = next(iter(finish_cols))
        self.player1_spawn = player1_spawn
        self.player2_spawn = player2_spawn
        self.finish_line_x = finish_col * TILE_SIZE + TILE_SIZE / 2
        self.finish_line_y_min = finish_rows[0] * TILE_SIZE
        self.finish_line_y_max = (finish_rows[-1] + 1) * TILE_SIZE

        # smooth diagonals with triangle walls using marching squares
        square_walls = [[tile.tile_type == 'wall' for tile in row] for row in self.grid]
        for row_idx in range(self.rows - 1):
            for col_idx in range(self.cols - 1):
                if square_walls[row_idx][col_idx] and square_walls[row_idx + 1][col_idx + 1]:
                    self.grid[row_idx][col_idx + 1].tile_type = 'tri_bottom_left'
                    self.grid[row_idx + 1][col_idx].tile_type = 'tri_top_right'
                if square_walls[row_idx][col_idx + 1] and square_walls[row_idx + 1][col_idx]:
                    self.grid[row_idx][col_idx].tile_type = 'tri_bottom_right'
                    self.grid[row_idx + 1][col_idx + 1].tile_type = 'tri_top_left'

    def get_tiles_in_rect(self, world_rect: pygame.Rect) -> list:
        """Returns all tiles intersecting a world-space rectangle."""
        col_start = max(0, world_rect.left // TILE_SIZE)
        col_end = min(self.cols, world_rect.right // TILE_SIZE + 1)
        row_start = max(0, world_rect.top // TILE_SIZE)
        row_end = min(self.rows, world_rect.bottom // TILE_SIZE + 1)
        return [
            self.grid[row][col]
            for row in range(row_start, row_end)
            for col in range(col_start, col_end)
        ]

    def build_surface(self) -> pygame.Surface:
        """Renders the static map into a world-space surface once for seam-free rotated blitting."""
        map_surface = pygame.Surface((self.pixel_width, self.pixel_height), pygame.SRCALPHA)
        for row in self.grid:
            for tile in row:
                info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                rect = tile.world_rect
                if info['shape'] == 'rect':
                    pygame.draw.rect(map_surface, info['color'], rect)
                    continue
                pygame.draw.rect(map_surface, TILE_TYPE_INFO['open']['color'], rect)
                max_idx = TILE_SIZE - 1
                tri_points = [
                    (int(rect.x + px * max_idx), int(rect.y + py * max_idx))
                    for px, py in info['points']
                ]
                pygame.draw.polygon(map_surface, info['color'], tri_points)
        return map_surface


# camera view that tracks a world-space point and a heading for rotated rendering
class Camera:
    def __init__(self, viewport_w: int, viewport_h: int):
        self.viewport_w: int = viewport_w
        self.viewport_h: int = viewport_h
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self.heading: float = 0.0

    def center_on(self, world_x: float, world_y: float):
        """Centers the camera on a world-space point."""
        self.offset_x = world_x - self.viewport_w / 2
        self.offset_y = world_y - self.viewport_h / 2

    def set_heading(self, heading: float):
        """Sets the camera heading to keep the vehicle front as up-screen."""
        self.heading = heading


# handles vehicle position, physics, and rendering
class Vehicle:
    def __init__(
        self,
        start_x: float,
        start_y: float,
        throttle_forward_key: int,
        throttle_back_key: int,
        steer_left_key: int,
        steer_right_key: int,
        color: tuple[int, int, int],
    ):
        self.world_x: float = start_x
        self.world_y: float = start_y
        self.vel_x: float = 0.0
        self.vel_y: float = 0.0
        self.throttle_input: int = 0
        self.steer_input: int = 0
        self.heading: float = 0.0
        self.ang_vel: float = 0.0
        self.prev_world_x: float = start_x
        self.prev_world_y: float = start_y
        self.curr_lap: int = 1
        self.max_lap: int = 1
        self.lap_timer: float = 0.0
        self.total_timer: float = 0.0
        self.race_finished: bool = False
        self.finish_place: int | None = None
        self.ignore_first_forward_cross: bool = True
        self.throttle_forward_key: int = throttle_forward_key
        self.throttle_back_key: int = throttle_back_key
        self.steer_left_key: int = steer_left_key
        self.steer_right_key: int = steer_right_key
        self.color: tuple[int, int, int] = color

    def get_bounding_rect(self) -> pygame.Rect:
        """Returns the world-space bounding rectangle of the vehicle."""
        return pygame.Rect(
            int(self.world_x) - PLAYER_SIZE // 2,
            int(self.world_y) - PLAYER_SIZE // 2,
            PLAYER_SIZE,
            PLAYER_SIZE,
        )

    def handle_input(self):
        """Updates throttle and steer inputs from currently pressed keys."""
        keys = pygame.key.get_pressed()
        self.throttle_input = int(keys[self.throttle_forward_key]) - int(keys[self.throttle_back_key])
        self.steer_input = int(keys[self.steer_right_key]) - int(keys[self.steer_left_key])

    def update_physics(self, dt: float):
        """Steps velocity and position using input acceleration and drag."""
        self.prev_world_x = self.world_x
        self.prev_world_y = self.world_y

        ang_acc = PLAYER_ANG_ACCEL * self.steer_input - PLAYER_ANG_DAMP * self.ang_vel
        self.ang_vel += ang_acc * dt
        self.ang_vel = clamp(self.ang_vel, -PLAYER_MAX_ANG_VEL, PLAYER_MAX_ANG_VEL)
        self.heading = (self.heading + self.ang_vel * dt) % (2.0 * math.pi)

        forward_accel = PLAYER_ACCEL * self.throttle_input
        accel_x = forward_accel * math.cos(self.heading) - PLAYER_DRAG * self.vel_x
        accel_y = forward_accel * math.sin(self.heading) - PLAYER_DRAG * self.vel_y

        self.vel_x += accel_x * dt
        self.vel_y += accel_y * dt
        self.vel_x = clamp(self.vel_x, -PLAYER_MAX_SPEED_SAFETY, PLAYER_MAX_SPEED_SAFETY)
        self.vel_y = clamp(self.vel_y, -PLAYER_MAX_SPEED_SAFETY, PLAYER_MAX_SPEED_SAFETY)

        self.world_x += self.vel_x * dt
        self.world_y += self.vel_y * dt

    def resolve_collisions(self, game_map: Map, triangle_masks: dict, player_mask: pygame.mask.Mask):
        """Resolves wall collisions using a rectangle-player collider and MTV response."""
        for _ in range(MAX_COLLISION_PASSES):
            corrected = False
            broad_rect = self.get_bounding_rect()
            for tile in game_map.get_tiles_in_rect(broad_rect):
                tile_info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                if not tile_info['is_wall']:
                    continue

                player_rect = self.get_bounding_rect()

                if tile_info['shape'] == 'tri':
                    if not triangle_mask_overlap(player_rect, tile.world_rect, tile.tile_type, triangle_masks, player_mask):
                        continue

                contact = rect_tile_contact_mtv(player_rect, tile.world_rect)
                if contact is None:
                    continue

                normal_x, normal_y, penetration = contact
                if penetration <= 0.0:
                    continue

                corrected = True
                push = penetration + COLLISION_SLOP
                self.world_x += normal_x * push
                self.world_y += normal_y * push

                vn = self.vel_x * normal_x + self.vel_y * normal_y
                if vn < 0.0:
                    bounce = (1.0 + PLAYER_RESTITUTION) * vn
                    self.vel_x -= bounce * normal_x
                    self.vel_y -= bounce * normal_y

            if not corrected:
                break

        if abs(self.vel_x) < 1e-3:
            self.vel_x = 0.0
        if abs(self.vel_y) < 1e-3:
            self.vel_y = 0.0

    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        """Draws the vehicle as a filled square centered on the given screen coordinates."""
        screen_rect = pygame.Rect(
            screen_x - PLAYER_SIZE // 2,
            screen_y - PLAYER_SIZE // 2,
            PLAYER_SIZE,
            PLAYER_SIZE,
        )
        pygame.draw.rect(surface, self.color, screen_rect)
        pygame.draw.rect(surface, (0, 0, 0), screen_rect, 1)


# =============================================================================
# Physics helpers
# =============================================================================

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def rect_tile_contact_mtv(player_rect: pygame.Rect, rect: pygame.Rect) -> tuple[float, float, float] | None:
    if not player_rect.colliderect(rect):
        return None

    overlap_left   = player_rect.right  - rect.left
    overlap_right  = rect.right  - player_rect.left
    overlap_top    = player_rect.bottom - rect.top
    overlap_bottom = rect.bottom - player_rect.top

    overlap_x = min(overlap_left, overlap_right)
    overlap_y = min(overlap_top, overlap_bottom)

    if overlap_x <= 0.0 or overlap_y <= 0.0:
        return None

    if overlap_x < overlap_y:
        if player_rect.centerx < rect.centerx:
            return (-1.0, 0.0, overlap_x)
        return (1.0, 0.0, overlap_x)

    if player_rect.centery < rect.centery:
        return (0.0, -1.0, overlap_y)
    return (0.0, 1.0, overlap_y)


def triangle_mask_overlap(
    player_rect: pygame.Rect,
    tile_rect: pygame.Rect,
    tile_type: str,
    triangle_masks: dict,
    player_mask: pygame.mask.Mask,
) -> bool:
    tri_mask = triangle_masks.get(tile_type)
    if tri_mask is None:
        return False
    offset = (player_rect.left - tile_rect.left, player_rect.top - tile_rect.top)
    return tri_mask.overlap(player_mask, offset) is not None


def update_lap_progress(player: Vehicle, game_map: Map) -> bool:
    """Advances lap state for player. Returns True when the race is completed."""
    if player.race_finished:
        return False

    finish_x = game_map.finish_line_x
    dx = player.world_x - player.prev_world_x
    crossed_finish_line = (
        (player.prev_world_x < finish_x <= player.world_x)
        or (player.prev_world_x > finish_x >= player.world_x)
    )

    y_at_cross = player.world_y
    if abs(dx) > 1e-6:
        t = clamp((finish_x - player.prev_world_x) / dx, 0.0, 1.0)
        y_at_cross = player.prev_world_y + t * (player.world_y - player.prev_world_y)

    y_in_finish_span = game_map.finish_line_y_min <= y_at_cross <= game_map.finish_line_y_max

    if y_in_finish_span and crossed_finish_line:
        if dx > 0.0:
            if player.ignore_first_forward_cross:
                player.ignore_first_forward_cross = False
                return False
            if player.curr_lap >= TOTAL_LAPS:
                player.race_finished = True
                return True
            player.curr_lap += 1
            if player.curr_lap > player.max_lap:
                player.max_lap = player.curr_lap
                player.lap_timer = 0.0
        elif dx < 0.0:
            player.curr_lap = max(1, player.curr_lap - 1)

    return False


# =============================================================================
# Render helpers
# =============================================================================

def make_button_rect(center_x: int, center_y: int, width: int = 220, height: int = 84) -> pygame.Rect:
    """Returns a Rect centered at the given coordinates."""
    rect = pygame.Rect(0, 0, width, height)
    rect.center = (center_x, center_y)
    return rect


def draw_button(
    screen: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    hovered: bool,
    bg_normal: tuple[int, int, int] = (210, 210, 210),
    bg_hover: tuple[int, int, int] = (235, 235, 235),
    text_color: tuple[int, int, int] = (0, 0, 0),
    border_color: tuple[int, int, int] = (0, 0, 0),
    border_width: int = 3,
):
    """Draws a single button with hover highlight."""
    bg_color = bg_hover if hovered else bg_normal
    pygame.draw.rect(screen, bg_color, rect)
    pygame.draw.rect(screen, border_color, rect, border_width)
    text_surface = font.render(label, True, text_color)
    text_rect = text_surface.get_rect(center=rect.center)
    screen.blit(text_surface, text_rect)


def render_status_screen(screen: pygame.Surface, title_font: pygame.font.Font, button_font: pygame.font.Font, message: str):
    """Renders a centered status message (connecting, waiting, etc.)."""
    screen.fill((25, 25, 25))
    title_surface = title_font.render('PiKart', True, (255, 255, 255))
    title_rect = title_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 5))
    screen.blit(title_surface, title_rect)
    msg_surface = button_font.render(message, True, (180, 180, 180))
    msg_rect = msg_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2))
    screen.blit(msg_surface, msg_rect)


def render_menu(
    screen: pygame.Surface,
    play_button_rect: pygame.Rect,
    quit_button_rect: pygame.Rect,
    title_font: pygame.font.Font,
    button_font: pygame.font.Font,
    error_msg: str | None,
):
    """Renders the main menu with Play and Quit buttons, and an optional error message."""
    screen.fill((25, 25, 25))
    title_surface = title_font.render('PiKart', True, (255, 255, 255))
    title_rect = title_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 5))
    screen.blit(title_surface, title_rect)

    if error_msg is not None:
        err_surface = button_font.render(error_msg, True, COLOR_ERROR)
        err_rect = err_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 120))
        screen.blit(err_surface, err_rect)

    mouse_pos = pygame.mouse.get_pos()
    draw_button(screen, play_button_rect, 'Play', button_font, play_button_rect.collidepoint(mouse_pos))
    draw_button(screen, quit_button_rect, 'Quit', button_font, quit_button_rect.collidepoint(mouse_pos))


def render_map_select(
    screen: pygame.Surface,
    map_names: list[str],
    map_row_rects: list[pygame.Rect],
    title_font: pygame.font.Font,
    button_font: pygame.font.Font,
    back_button_rect: pygame.Rect,
):
    """Renders the map selection screen with one clickable row per discovered map."""
    screen.fill((25, 25, 25))
    title_surface = title_font.render('Select Map', True, (255, 255, 255))
    title_rect = title_surface.get_rect(center=(VIEWPORT_WIDTH // 2, 80))
    screen.blit(title_surface, title_rect)

    mouse_pos = pygame.mouse.get_pos()
    for name, rect in zip(map_names, map_row_rects):
        draw_button(
            screen, rect, name, button_font, rect.collidepoint(mouse_pos),
            bg_normal=(50, 50, 80), bg_hover=(80, 80, 130),
            text_color=(255, 255, 255), border_color=(120, 120, 180),
        )

    if not map_names:
        no_maps_surface = button_font.render('No .txt maps found in current directory.', True, COLOR_ERROR)
        no_maps_rect = no_maps_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2))
        screen.blit(no_maps_surface, no_maps_rect)

    draw_button(screen, back_button_rect, '< Back', button_font, back_button_rect.collidepoint(mouse_pos))


def render_post_race(
    screen: pygame.Surface,
    play_again_rect: pygame.Rect,
    menu_button_rect: pygame.Rect,
    title_font: pygame.font.Font,
    button_font: pygame.font.Font,
):
    """Renders the post-race overlay with Play Again and Exit to Menu buttons."""
    overlay = pygame.Surface((VIEWPORT_WIDTH, VIEWPORT_HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    screen.blit(overlay, (0, 0))
    title_surface = title_font.render('Race Over!', True, (255, 255, 255))
    title_rect = title_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 80))
    screen.blit(title_surface, title_rect)
    mouse_pos = pygame.mouse.get_pos()
    draw_button(
        screen, play_again_rect, 'Play Again', button_font, play_again_rect.collidepoint(mouse_pos),
        bg_normal=(60, 140, 60), bg_hover=(90, 185, 90),
        text_color=(255, 255, 255), border_color=(30, 90, 30),
    )
    draw_button(screen, menu_button_rect, 'Exit to Menu', button_font, menu_button_rect.collidepoint(mouse_pos))


def render_center_overlay_message(screen: pygame.Surface, message: str, font: pygame.font.Font):
    """Renders a centered text box overlay."""
    text_surface = font.render(message, True, (0, 0, 0))
    padding_x = 24
    padding_y = 14
    box_rect = pygame.Rect(
        VIEWPORT_WIDTH // 2 - (text_surface.get_width() + 2 * padding_x) // 2,
        VIEWPORT_HEIGHT // 2 - (text_surface.get_height() + 2 * padding_y) // 2,
        text_surface.get_width() + 2 * padding_x,
        text_surface.get_height() + 2 * padding_y,
    )
    pygame.draw.rect(screen, (255, 255, 255), box_rect)
    pygame.draw.rect(screen, (0, 0, 0), box_rect, 3)
    text_rect = text_surface.get_rect(center=box_rect.center)
    screen.blit(text_surface, text_rect)


def render_map(
    screen: pygame.Surface,
    map_surface: pygame.Surface,
    camera: Camera,
    viewport_rect: pygame.Rect | None = None,
    overlay_vehicles: list | None = None,
):
    """Renders the map patch visible through the camera, with optional vehicle overlays."""
    if viewport_rect is None:
        viewport_rect = pygame.Rect(0, 0, camera.viewport_w, camera.viewport_h)

    diag = (
        int(math.ceil(math.sqrt(camera.viewport_w ** 2 + camera.viewport_h ** 2)))
        + 2 * TILE_SIZE
    )
    patch_surface = pygame.Surface((diag, diag), pygame.SRCALPHA)

    camera_center_world_x = camera.offset_x + camera.viewport_w / 2.0
    camera_center_world_y = camera.offset_y + camera.viewport_h / 2.0
    patch_left_world = camera_center_world_x - diag / 2.0
    patch_top_world  = camera_center_world_y - diag / 2.0

    patch_surface.blit(
        map_surface,
        (-int(round(patch_left_world)), -int(round(patch_top_world))),
    )

    if overlay_vehicles is not None:
        for v in overlay_vehicles:
            local_x = int(round(v.world_x - patch_left_world))
            local_y = int(round(v.world_y - patch_top_world))
            v.draw(patch_surface, local_x, local_y)

    angle_deg = math.degrees(camera.heading) + 90.0
    rotated = pygame.transform.rotate(patch_surface, angle_deg)
    rotated_rect = rotated.get_rect(center=viewport_rect.center)
    screen.blit(rotated, rotated_rect)


def render_hud(
    screen: pygame.Surface,
    viewport_rect: pygame.Rect,
    max_lap: int,
    total_laps: int,
    total_timer: float,
    lap_timer: float,
    finish_place: int | None,
    hud_font: pygame.font.Font,
    place_font: pygame.font.Font,
):
    """Renders lap counter, timers, and finish place for one player's viewport."""
    render_lap_counter(screen, viewport_rect, max_lap, total_laps, hud_font)
    render_time_hud(screen, viewport_rect, total_timer, lap_timer, hud_font)
    render_finish_place(screen, viewport_rect, finish_place, place_font)


def render_lap_counter(
    screen: pygame.Surface,
    viewport_rect: pygame.Rect,
    max_lap: int,
    total_laps: int,
    font: pygame.font.Font,
):
    width = 92
    height = 44
    margin = 10
    box_rect = pygame.Rect(
        viewport_rect.right - margin - width,
        viewport_rect.top + margin,
        width,
        height,
    )
    pygame.draw.rect(screen, (255, 255, 255), box_rect)
    pygame.draw.rect(screen, (0, 0, 0), box_rect, 2)
    shown_lap = min(max_lap, total_laps)
    text_surface = font.render(f"{shown_lap}/{total_laps}", True, (0, 0, 0))
    text_rect = text_surface.get_rect(center=box_rect.center)
    screen.blit(text_surface, text_rect)


def render_time_hud(
    screen: pygame.Surface,
    viewport_rect: pygame.Rect,
    total_timer: float,
    lap_timer: float,
    font: pygame.font.Font,
):
    total_minutes = int(total_timer // 60.0)
    total_seconds = total_timer % 60.0
    total_text = f"T: {total_minutes}:{total_seconds:05.2f}"
    minutes = int(lap_timer // 60.0)
    seconds = lap_timer % 60.0
    lap_text = f"L: {minutes}:{seconds:05.2f}"

    total_surface = font.render(total_text, True, (0, 140, 0))
    lap_surface   = font.render(lap_text,   True, (0, 140, 0))

    content_width  = max(total_surface.get_width(), lap_surface.get_width())
    content_height = total_surface.get_height() + lap_surface.get_height() + 2
    padding_x = 10
    padding_y = 6
    box_rect = pygame.Rect(
        viewport_rect.centerx - (content_width + 2 * padding_x) // 2,
        viewport_rect.top + 6,
        content_width + 2 * padding_x,
        content_height + 2 * padding_y,
    )
    pygame.draw.rect(screen, (255, 255, 255), box_rect)
    pygame.draw.rect(screen, (0, 0, 0), box_rect, 2)

    total_rect = total_surface.get_rect(centerx=box_rect.centerx, top=box_rect.top + padding_y)
    screen.blit(total_surface, total_rect)
    lap_rect = lap_surface.get_rect(centerx=box_rect.centerx, top=total_rect.bottom + 2)
    screen.blit(lap_surface, lap_rect)


def format_place(place: int) -> str:
    if 10 <= (place % 100) <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(place % 10, 'th')
    return f"{place}{suffix}"


def render_finish_place(
    screen: pygame.Surface,
    viewport_rect: pygame.Rect,
    finish_place: int | None,
    font: pygame.font.Font,
):
    if finish_place is None:
        return
    text_surface = font.render(format_place(finish_place), True, (0, 0, 0))
    padding_x = 14
    padding_y = 10
    box_rect = pygame.Rect(
        viewport_rect.centerx - (text_surface.get_width() + 2 * padding_x) // 2,
        viewport_rect.centery - 40,
        text_surface.get_width() + 2 * padding_x,
        text_surface.get_height() + 2 * padding_y,
    )
    pygame.draw.rect(screen, (255, 255, 255), box_rect)
    pygame.draw.rect(screen, (0, 0, 0), box_rect, 2)
    text_rect = text_surface.get_rect(center=box_rect.center)
    screen.blit(text_surface, text_rect)


def scan_map_files() -> list[str]:
    """Returns sorted list of map stems (filename without .txt) in the current directory."""
    entries = []
    for name in os.listdir('.'):
        if name.lower().endswith('.txt') and os.path.isfile(name):
            entries.append(os.path.splitext(name)[0])
    return sorted(entries)


def build_map_row_rects(count: int) -> list[pygame.Rect]:
    """Returns vertically stacked button rects for the map selection list."""
    row_height = 68
    start_y = 160
    cx = VIEWPORT_WIDTH // 2
    return [
        make_button_rect(cx, start_y + i * row_height, width=360, height=54)
        for i in range(count)
    ]