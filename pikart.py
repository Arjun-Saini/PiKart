import pygame
import sys
import os
import math

# =============================================================================
# Constants
# =============================================================================

# display
VIEWPORT_WIDTH = 800
VIEWPORT_HEIGHT = 600
FPS = 60
TOTAL_LAPS = 3

STATE_MENU = 'menu'
STATE_GAME = 'game'
COUNTDOWN_DURATION = 3.0
GO_DISPLAY_DURATION = 0.75
POST_RACE_RETURN_DELAY = 5.0

# map
MAP_FILE = 'map.txt'
TILE_SIZE = 10

# player vehicle
PLAYER_SIZE = 10
PLAYER_ACCEL = 900.0
PLAYER_DRAG = 2.0
PLAYER_MAX_SPEED_SAFETY = 500.0
MAX_PHYSICS_DT = 1.0 / 30.0
PLAYER_RESTITUTION = 0.3
COLLISION_SLOP = 0.05
MAX_COLLISION_PASSES = 2
PLAYER_ANG_ACCEL = 10.0
PLAYER_ANG_DAMP = 5.0
PLAYER_MAX_ANG_VEL = 4.5

# colors
COLOR_BACKGROUND = ( 30,  30,  30)
COLOR_OPEN       = (255, 255, 255)
COLOR_WALL       = (  0,   0,   0)
COLOR_TRIANGLE   = (  0,   0,   0)
COLOR_POWERUP1   = (255, 220,   0)
COLOR_POWERUP2   = (120, 220, 255)
COLOR_PLAYER     = (220,  30,  30)
COLOR_PLAYER2    = ( 30, 100, 220)
COLOR_SPAWN      = (50,  100,  50)
COLOR_FINISH     = (180, 180, 180)

# tile information
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


def _build_triangle_mask(normalized_points: tuple[tuple[float, float], ...]) -> pygame.mask.Mask:
    surface = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
    max_idx = TILE_SIZE - 1
    pixel_points = [
        (int(px * max_idx), int(py * max_idx))
        for px, py in normalized_points
    ]
    pygame.draw.polygon(surface, (255, 255, 255), pixel_points)
    return pygame.mask.from_surface(surface)


def _build_player_rect_mask() -> pygame.mask.Mask:
    size = PLAYER_SIZE
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.rect(surface, (255, 255, 255), surface.get_rect())
    return pygame.mask.from_surface(surface)


TRIANGLE_MASKS = {
    tile_type: _build_triangle_mask(tile_info['points'])
    for tile_type, tile_info in TILE_TYPE_INFO.items()
    if tile_info.get('shape') == 'tri'
}
PLAYER_COLLISION_MASK = _build_player_rect_mask()

CHAR_TO_TILE_TYPE = {
    '#': 'wall',
    '*': 'powerup1',
    '+': 'powerup2',
    '1': 'player1_spawn',
    '2': 'player2_spawn',
    '|': 'finish_line',
}


# =============================================================================
# Game objects
# =============================================================================

# represents one map tile and renders it based on tile_type.
class Tile:
    def __init__(self, tile_type: str, grid_x: int, grid_y: int):
        self.tile_type: str = tile_type
        self.grid_x: int = grid_x
        self.grid_y: int = grid_y
        self.world_rect: pygame.Rect = pygame.Rect(grid_x * TILE_SIZE, grid_y * TILE_SIZE, TILE_SIZE, TILE_SIZE)

    # draws this tile in screen space using camera transform.
    def draw(self, surface: pygame.Surface, camera):
        info = TILE_TYPE_INFO.get(self.tile_type, TILE_TYPE_INFO['open'])
        rect = self.world_rect
        rect_points_world = [
            (rect.left, rect.top),
            (rect.right, rect.top),
            (rect.right, rect.bottom),
            (rect.left, rect.bottom),
        ]
        rect_points_screen = [camera.world_to_screen_point(x, y) for x, y in rect_points_world]

        if info['shape'] == 'rect':
            pygame.draw.polygon(surface, info['color'], rect_points_screen)
            return

        # Draw base open tile first, then triangle overlay to preserve triangle semantics.
        pygame.draw.polygon(surface, TILE_TYPE_INFO['open']['color'], rect_points_screen)
        tri_points_world = [
            (rect.x + px * TILE_SIZE, rect.y + py * TILE_SIZE)
            for px, py in info['points']
        ]
        tri_points_screen = [camera.world_to_screen_point(x, y) for x, y in tri_points_world]
        pygame.draw.polygon(surface, info['color'], tri_points_screen)


# converts a map file into a tile matrix
class Map:
    def __init__(self, filepath: str):
        self.grid: list[list] = []
        self.rows: int = 0
        self.cols: int = 0
        self.pixel_width: int = 0
        self.pixel_height: int = 0
        self.player1_spawn: tuple[float, float] | None = None
        self.player2_spawn: tuple[float, float] | None = None
        self.finish_line_x: float | None = None
        self.finish_line_y_min: float | None = None
        self.finish_line_y_max: float | None = None
        player1_spawn_count = 0
        player2_spawn_count = 0
        finish_tiles: list[tuple[int, int]] = []

        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Map file not found: {filepath}")

        with open(filepath, 'r') as f:
            lines = f.read().splitlines()

        self.rows = len(lines)
        self.cols = max(len(line) for line in lines)
        self.pixel_width = self.cols * TILE_SIZE
        self.pixel_height = self.rows * TILE_SIZE

        # assign each tile directly from the map characters
        for row_idx, line in enumerate(lines):
            row = []
            for col_idx in range(self.cols):
                tile_char = line[col_idx] if col_idx < len(line) else ' '
                tile_type = CHAR_TO_TILE_TYPE.get(tile_char, 'open')
                
                if tile_type == 'player1_spawn':
                    player1_spawn_count += 1
                    self.player1_spawn = (
                        col_idx * TILE_SIZE + TILE_SIZE / 2,
                        row_idx * TILE_SIZE + TILE_SIZE / 2,
                    )
                elif tile_type == 'player2_spawn':
                    player2_spawn_count += 1
                    self.player2_spawn = (
                        col_idx * TILE_SIZE + TILE_SIZE / 2,
                        row_idx * TILE_SIZE + TILE_SIZE / 2,
                    )
                elif tile_type == 'finish_line':
                    finish_tiles.append((row_idx, col_idx))

                row.append(Tile(tile_type=tile_type, grid_x=col_idx, grid_y=row_idx))
            self.grid.append(row)

        if player1_spawn_count != 1:
            raise ValueError(f"Map must contain exactly one '1' spawn marker, found {player1_spawn_count}.")
        if player2_spawn_count != 1:
            raise ValueError(f"Map must contain exactly one '2' spawn marker, found {player2_spawn_count}.")

        if len(finish_tiles) != 7:
            raise ValueError(f"Map must contain exactly seven '|' finish markers, found {len(finish_tiles)}.")

        finish_cols = {col for _, col in finish_tiles}
        if len(finish_cols) != 1:
            raise ValueError("Finish markers must form a single vertical line (all '|' in one column).")

        finish_rows = sorted(row for row, _ in finish_tiles)
        if any(curr != prev + 1 for prev, curr in zip(finish_rows, finish_rows[1:])):
            raise ValueError("Finish markers must be contiguous vertically.")

        finish_col = next(iter(finish_cols))
        self.finish_line_x = finish_col * TILE_SIZE + TILE_SIZE / 2
        self.finish_line_y_min = finish_rows[0] * TILE_SIZE
        self.finish_line_y_max = (finish_rows[-1] + 1) * TILE_SIZE

        # smooth diagonals with triangle walls
        square_walls = [[tile.tile_type == 'wall' for tile in row] for row in self.grid]

        for row_idx in range(self.rows - 1):
            for col_idx in range(self.cols - 1):
                if square_walls[row_idx][col_idx] and square_walls[row_idx + 1][col_idx + 1]:
                    self.grid[row_idx][col_idx + 1].tile_type = 'tri_bottom_left'
                    self.grid[row_idx + 1][col_idx].tile_type = 'tri_top_right'

                if square_walls[row_idx][col_idx + 1] and square_walls[row_idx + 1][col_idx]:
                    self.grid[row_idx][col_idx].tile_type = 'tri_bottom_right'
                    self.grid[row_idx + 1][col_idx + 1].tile_type = 'tri_top_left'

    # returns the tile at the specified coordinates
    def tile_at(self, grid_x: int, grid_y: int):
        if 0 <= grid_y < self.rows and 0 <= grid_x < self.cols:
            return self.grid[grid_y][grid_x]
        return None

    # returns all tiles intersecting a world-space rectangle
    def get_tiles_in_rect(self, world_rect: pygame.Rect) -> list:
        col_start = max(0, world_rect.left // TILE_SIZE)
        col_end = min(self.cols, world_rect.right // TILE_SIZE + 1)
        row_start = max(0, world_rect.top // TILE_SIZE)
        row_end = min(self.rows, world_rect.bottom // TILE_SIZE + 1)
        return [self.grid[row][col] for row in range(row_start, row_end) for col in range(col_start, col_end)]

    # renders the static map into a world-space surface once for seam-free rotated blitting.
    def build_surface(self) -> pygame.Surface:
        map_surface = pygame.Surface((self.pixel_width, self.pixel_height), pygame.SRCALPHA)

        for row in self.grid:
            for tile in row:
                info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                rect = tile.world_rect

                if info['shape'] == 'rect':
                    pygame.draw.rect(map_surface, info['color'], rect)
                    continue

                # Draw base open tile first, then triangle overlay for corner semantics.
                pygame.draw.rect(map_surface, TILE_TYPE_INFO['open']['color'], rect)
                max_idx = TILE_SIZE - 1
                tri_points = [
                    (
                        int(rect.x + px * max_idx),
                        int(rect.y + py * max_idx),
                    )
                    for px, py in info['points']
                ]
                pygame.draw.polygon(map_surface, info['color'], tri_points)

        return map_surface


# camera view that converts between world and screen coordinates
class Camera:
    def __init__(self, viewport_w: int, viewport_h: int):
        self.viewport_w: int = viewport_w
        self.viewport_h: int = viewport_h
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self.heading: float = 0.0

    # centers the camera on a world-space point.
    def center_on(self, world_x: float, world_y: float):
        self.offset_x = world_x - self.viewport_w / 2
        self.offset_y = world_y - self.viewport_h / 2

    # sets camera heading to keep vehicle front as up-screen in rendering.
    def set_heading(self, heading: float):
        self.heading = heading

    # returns the currently visible world-space rectangle.
    def get_world_rect(self) -> pygame.Rect:
        # Expand culling bounds for rotated camera view to prevent edge pop-in.
        diag = int(math.sqrt(self.viewport_w * self.viewport_w + self.viewport_h * self.viewport_h))
        margin = max((diag - min(self.viewport_w, self.viewport_h)) // 2, TILE_SIZE)
        return pygame.Rect(
            int(self.offset_x) - margin,
            int(self.offset_y) - margin,
            self.viewport_w + 2 * margin,
            self.viewport_h + 2 * margin,
        )

    # converts a world-space point to screen coordinates with camera-heading rotation.
    def world_to_screen_point(self, world_x: float, world_y: float) -> tuple[int, int]:
        center_world_x = self.offset_x + self.viewport_w / 2
        center_world_y = self.offset_y + self.viewport_h / 2
        dx = world_x - center_world_x
        dy = world_y - center_world_y

        # Forward vector of vehicle in world coordinates.
        fwd_x = math.cos(self.heading)
        fwd_y = math.sin(self.heading)
        right_x = fwd_y
        right_y = -fwd_x

        screen_x = dx * right_x + dy * right_y + self.viewport_w / 2
        screen_y = -(dx * fwd_x + dy * fwd_y) + self.viewport_h / 2
        return (round(screen_x), round(screen_y))


# handles vehicle position, movement, and rendering surface
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
        self.finish_time: float | None = None
        self.finish_place: int | None = None
        self.ignore_first_forward_cross: bool = True
        self.throttle_forward_key: int = throttle_forward_key
        self.throttle_back_key: int = throttle_back_key
        self.steer_left_key: int = steer_left_key
        self.steer_right_key: int = steer_right_key
        self.color: tuple[int, int, int] = color

        self.surface: pygame.Surface = pygame.Surface((PLAYER_SIZE, PLAYER_SIZE), pygame.SRCALPHA)
        self.surface.fill(self.color)
        pygame.draw.rect(self.surface, (0, 0, 0), self.surface.get_rect(), 1)

    # returns the world-space rectangle of the player
    def get_bounding_rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.world_x) - PLAYER_SIZE // 2, int(self.world_y) - PLAYER_SIZE // 2, PLAYER_SIZE, PLAYER_SIZE)

    # updates input direction from arrow keys
    def handle_input(self):
        keys = pygame.key.get_pressed()

        self.throttle_input = int(keys[self.throttle_forward_key]) - int(keys[self.throttle_back_key])
        self.steer_input = int(keys[self.steer_right_key]) - int(keys[self.steer_left_key])

    # steps velocity and position using input acceleration and drag
    def update_physics(self, dt: float):
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

    # resolves wall collisions using a rectangle-player collider and MTV response.
    def resolve_collisions(self, game_map: Map):
        for _ in range(MAX_COLLISION_PASSES):
            corrected = False
            broad_rect = self.get_bounding_rect()
            for tile in game_map.get_tiles_in_rect(broad_rect):
                tile_info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                if not tile_info['is_wall']:
                    continue

                player_rect = self.get_bounding_rect()
                contact = self._contact_with_tile(tile, tile_info, player_rect)
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

    def _contact_with_tile(self, tile: Tile, tile_info: dict, player_rect: pygame.Rect) -> tuple[float, float, float] | None:
        if tile_info['shape'] == 'tri':
            if not triangle_mask_overlap(player_rect, tile.world_rect, tile.tile_type):
                return None
        return rect_tile_contact_mtv(player_rect, tile.world_rect)

    # draws the player through the camera transform.
    def draw(self, surface: pygame.Surface, camera, viewport_rect: pygame.Rect | None = None):
        if viewport_rect is None:
            viewport_rect = pygame.Rect(0, 0, camera.viewport_w, camera.viewport_h)

        screen_rect = pygame.Rect(
            viewport_rect.centerx - PLAYER_SIZE // 2,
            viewport_rect.centery - PLAYER_SIZE // 2,
            PLAYER_SIZE,
            PLAYER_SIZE,
        )
        pygame.draw.rect(surface, self.color, screen_rect)
        pygame.draw.rect(surface, (0, 0, 0), screen_rect, 1)


# =============================================================================
# Helpers
# =============================================================================

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def rect_tile_contact_mtv(player_rect: pygame.Rect, rect: pygame.Rect) -> tuple[float, float, float] | None:
    if not player_rect.colliderect(rect):
        return None

    overlap_left = player_rect.right - rect.left
    overlap_right = rect.right - player_rect.left
    overlap_top = player_rect.bottom - rect.top
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


def triangle_mask_overlap(player_rect: pygame.Rect, tile_rect: pygame.Rect, tile_type: str) -> bool:
    tri_mask = TRIANGLE_MASKS.get(tile_type)
    if tri_mask is None:
        return False

    offset = (player_rect.left - tile_rect.left, player_rect.top - tile_rect.top)
    return tri_mask.overlap(PLAYER_COLLISION_MASK, offset) is not None


def update_lap_progress(player: Vehicle, game_map: Map) -> bool:
    if player.race_finished:
        return False

    if game_map.finish_line_x is None or game_map.finish_line_y_min is None or game_map.finish_line_y_max is None:
        return False

    finish_x = game_map.finish_line_x
    dx = player.world_x - player.prev_world_x
    crossed_finish_line = (
        (player.prev_world_x < finish_x <= player.world_x)
        or (player.prev_world_x > finish_x >= player.world_x)
    )

    y_at_cross = player.world_y
    if abs(dx) > 1e-6:
        t = (finish_x - player.prev_world_x) / dx
        t = clamp(t, 0.0, 1.0)
        y_at_cross = player.prev_world_y + t * (player.world_y - player.prev_world_y)

    y_in_finish_span = game_map.finish_line_y_min <= y_at_cross <= game_map.finish_line_y_max

    if y_in_finish_span and crossed_finish_line:
        if dx > 0.0:
            # Players spawn just left of the line; ignore the first forward crossing.
            if player.ignore_first_forward_cross:
                player.ignore_first_forward_cross = False
                return False

            # Crossing forward while already on final lap completes the race.
            if player.curr_lap >= TOTAL_LAPS:
                player.race_finished = True
                player.finish_time = player.total_timer
                return True

            player.curr_lap += 1
            if player.curr_lap > player.max_lap:
                player.max_lap = player.curr_lap
                player.lap_timer = 0.0
        elif dx < 0.0:
            player.curr_lap = max(1, player.curr_lap - 1)

    return False


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
    lap_surface = font.render(lap_text, True, (0, 140, 0))

    content_width = max(total_surface.get_width(), lap_surface.get_width())
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

    total_rect = total_surface.get_rect(
        centerx=box_rect.centerx,
        top=box_rect.top + padding_y,
    )
    screen.blit(total_surface, total_rect)

    lap_rect = lap_surface.get_rect(
        centerx=box_rect.centerx,
        top=total_rect.bottom + 2,
    )
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


def render_menu(
    screen: pygame.Surface,
    start_button_rect: pygame.Rect,
    title_font: pygame.font.Font,
    button_font: pygame.font.Font,
):
    screen.fill((25, 25, 25))

    title_surface = title_font.render('PiKart', True, (255, 255, 255))
    title_rect = title_surface.get_rect(center=(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 3))
    screen.blit(title_surface, title_rect)

    mouse_pos = pygame.mouse.get_pos()
    button_color = (235, 235, 235) if start_button_rect.collidepoint(mouse_pos) else (210, 210, 210)
    pygame.draw.rect(screen, button_color, start_button_rect)
    pygame.draw.rect(screen, (0, 0, 0), start_button_rect, 3)

    button_text = button_font.render('Start', True, (0, 0, 0))
    button_text_rect = button_text.get_rect(center=start_button_rect.center)
    screen.blit(button_text, button_text_rect)


def render_center_overlay_message(
    screen: pygame.Surface,
    message: str,
    font: pygame.font.Font,
):
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


def create_race_objects(
    game_map: Map,
    left_viewport: pygame.Rect,
    right_viewport: pygame.Rect,
) -> tuple[Vehicle, Vehicle, Camera, Camera, int]:
    player1 = Vehicle(
        *(game_map.player1_spawn),
        throttle_forward_key=pygame.K_UP,
        throttle_back_key=pygame.K_DOWN,
        steer_left_key=pygame.K_LEFT,
        steer_right_key=pygame.K_RIGHT,
        color=COLOR_PLAYER,
    )
    player2 = Vehicle(
        *(game_map.player2_spawn),
        throttle_forward_key=pygame.K_w,
        throttle_back_key=pygame.K_s,
        steer_left_key=pygame.K_a,
        steer_right_key=pygame.K_d,
        color=COLOR_PLAYER2,
    )

    camera1 = Camera(left_viewport.width, left_viewport.height)
    camera2 = Camera(right_viewport.width, right_viewport.height)
    camera1.center_on(player1.world_x, player1.world_y)
    camera1.set_heading(player1.heading)
    camera2.center_on(player2.world_x, player2.world_y)
    camera2.set_heading(player2.heading)

    next_finish_place = 1
    return player1, player2, camera1, camera2, next_finish_place

# renders only map tiles that are inside the camera viewport
def render_map(
    screen: pygame.Surface,
    map_surface: pygame.Surface,
    camera: Camera,
    viewport_rect: pygame.Rect | None = None,
    overlay_players: list[Vehicle] | None = None,
):
    if viewport_rect is None:
        viewport_rect = pygame.Rect(0, 0, camera.viewport_w, camera.viewport_h)

    # Build a camera-centered square patch, then rotate it around the screen center.
    diag = int(math.ceil(math.sqrt(camera.viewport_w * camera.viewport_w + camera.viewport_h * camera.viewport_h))) + 2 * TILE_SIZE
    patch_surface = pygame.Surface((diag, diag), pygame.SRCALPHA)

    camera_center_world_x = camera.offset_x + camera.viewport_w / 2.0
    camera_center_world_y = camera.offset_y + camera.viewport_h / 2.0
    patch_left_world = camera_center_world_x - diag / 2.0
    patch_top_world = camera_center_world_y - diag / 2.0

    patch_surface.blit(
        map_surface,
        (-int(round(patch_left_world)), -int(round(patch_top_world))),
    )

    if overlay_players is not None:
        for overlay_player in overlay_players:
            local_x = int(round(overlay_player.world_x - patch_left_world))
            local_y = int(round(overlay_player.world_y - patch_top_world))
            remote_rect = pygame.Rect(
                local_x - PLAYER_SIZE // 2,
                local_y - PLAYER_SIZE // 2,
                PLAYER_SIZE,
                PLAYER_SIZE,
            )
            pygame.draw.rect(patch_surface, overlay_player.color, remote_rect)
            pygame.draw.rect(patch_surface, (0, 0, 0), remote_rect, 1)

    angle_deg = math.degrees(camera.heading) + 90.0
    rotated_map = pygame.transform.rotate(patch_surface, angle_deg)
    rotated_rect = rotated_map.get_rect(center=viewport_rect.center)
    screen.blit(rotated_map, rotated_rect)

# renders the player through the camera transform
def render_player(screen: pygame.Surface, player: Vehicle, camera: Camera, viewport_rect: pygame.Rect | None = None):
    player.draw(screen, camera, viewport_rect)


# =============================================================================
# Game logic
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption("PiKart")
clock = pygame.time.Clock()
hud_font = pygame.font.SysFont(None, 30)
place_font = pygame.font.SysFont(None, 54)
countdown_font = pygame.font.SysFont(None, 110)
menu_title_font = pygame.font.SysFont(None, 92)
menu_button_font = pygame.font.SysFont(None, 48)

game_map = Map(MAP_FILE)
map_surface = game_map.build_surface()
half_viewport_w = VIEWPORT_WIDTH // 2
left_viewport = pygame.Rect(0, 0, half_viewport_w, VIEWPORT_HEIGHT)
right_viewport = pygame.Rect(half_viewport_w, 0, VIEWPORT_WIDTH - half_viewport_w, VIEWPORT_HEIGHT)
start_button_rect = pygame.Rect(0, 0, 220, 84)
start_button_rect.center = (VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2)

player1, player2, camera1, camera2, next_finish_place = create_race_objects(game_map, left_viewport, right_viewport)

current_state = STATE_MENU
countdown_remaining = 0.0
go_display_remaining = 0.0
post_race_return_remaining: float | None = None

running = True
while running:
    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            running = False
        elif current_state == STATE_MENU and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if start_button_rect.collidepoint(event.pos):
                player1, player2, camera1, camera2, next_finish_place = create_race_objects(game_map, left_viewport, right_viewport)
                current_state = STATE_GAME
                countdown_remaining = COUNTDOWN_DURATION
                go_display_remaining = 0.0
                post_race_return_remaining = None

    if current_state == STATE_MENU:
        render_menu(screen, start_button_rect, menu_title_font, menu_button_font)
        pygame.display.flip()
        continue

    if countdown_remaining > 0.0:
        countdown_remaining = max(0.0, countdown_remaining - dt)
        if countdown_remaining == 0.0:
            go_display_remaining = GO_DISPLAY_DURATION

    if go_display_remaining > 0.0:
        go_display_remaining = max(0.0, go_display_remaining - dt)

    input_enabled = countdown_remaining <= 0.0

    if input_enabled:
        player1.handle_input()
        player2.handle_input()

        player1.update_physics(dt)
        player2.update_physics(dt)

        player1_just_finished = update_lap_progress(player1, game_map)
        player2_just_finished = update_lap_progress(player2, game_map)

        if player1_just_finished and player1.finish_place is None:
            player1.finish_place = next_finish_place
            next_finish_place += 1
        if player2_just_finished and player2.finish_place is None:
            player2.finish_place = next_finish_place
            next_finish_place += 1

        if not player1.race_finished:
            player1.total_timer += dt
            player1.lap_timer += dt
        if not player2.race_finished:
            player2.total_timer += dt
            player2.lap_timer += dt

        player1.resolve_collisions(game_map)
        player2.resolve_collisions(game_map)

        if player1.race_finished and player2.race_finished:
            if post_race_return_remaining is None:
                post_race_return_remaining = POST_RACE_RETURN_DELAY
            else:
                post_race_return_remaining = max(0.0, post_race_return_remaining - dt)
                if post_race_return_remaining == 0.0:
                    current_state = STATE_MENU
                    countdown_remaining = 0.0
                    go_display_remaining = 0.0
                    post_race_return_remaining = None
                    continue
    else:
        player1.throttle_input = 0
        player1.steer_input = 0
        player2.throttle_input = 0
        player2.steer_input = 0

    camera1.center_on(player1.world_x, player1.world_y)
    camera1.set_heading(player1.heading)
    camera2.center_on(player2.world_x, player2.world_y)
    camera2.set_heading(player2.heading)

    screen.fill(COLOR_BACKGROUND)

    screen.set_clip(left_viewport)
    render_map(screen, map_surface, camera1, left_viewport, overlay_players=[player2])
    render_player(screen, player1, camera1, left_viewport)
    render_lap_counter(screen, left_viewport, player1.max_lap, TOTAL_LAPS, hud_font)
    render_time_hud(screen, left_viewport, player1.total_timer, player1.lap_timer, hud_font)
    render_finish_place(screen, left_viewport, player1.finish_place, place_font)

    screen.set_clip(right_viewport)
    render_map(screen, map_surface, camera2, right_viewport, overlay_players=[player1])
    render_player(screen, player2, camera2, right_viewport)
    render_lap_counter(screen, right_viewport, player2.max_lap, TOTAL_LAPS, hud_font)
    render_time_hud(screen, right_viewport, player2.total_timer, player2.lap_timer, hud_font)
    render_finish_place(screen, right_viewport, player2.finish_place, place_font)

    screen.set_clip(None)
    pygame.draw.line(screen, (0, 0, 0), (half_viewport_w, 0), (half_viewport_w, VIEWPORT_HEIGHT), 2)

    if countdown_remaining > 0.0:
        countdown_number = int(math.ceil(countdown_remaining))
        render_center_overlay_message(screen, str(countdown_number), countdown_font)
    elif go_display_remaining > 0.0:
        render_center_overlay_message(screen, 'GO!', countdown_font)

    pygame.display.flip()

pygame.quit()
sys.exit()