import pygame
import sys
import os

# =============================================================================
# Constants
# =============================================================================

# display
VIEWPORT_WIDTH = 800
VIEWPORT_HEIGHT = 600
FPS = 60

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

# colors
COLOR_BACKGROUND = ( 30,  30,  30)
COLOR_OPEN       = (255, 255, 255)
COLOR_WALL       = (  0,   0,   0)
COLOR_TRIANGLE   = (  0,   0,   0)
COLOR_POWERUP1   = (255, 220,   0)
COLOR_POWERUP2   = (120, 220, 255)
COLOR_PLAYER     = (220,  30,  30)
COLOR_SPAWN      = (50,  100,  50)

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
    '1': 'player1_spawn'
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

    # draws this tile in screen space using tile metadata.
    def draw(self, surface: pygame.Surface, screen_rect: pygame.Rect):
        info = TILE_TYPE_INFO.get(self.tile_type, TILE_TYPE_INFO['open'])
        if info['shape'] == 'rect':
            pygame.draw.rect(surface, info['color'], screen_rect)
            return

        # avoid pixels clipping on tile boundaries
        x, y, w, h = screen_rect
        max_x = max(w - 1, 0)
        max_y = max(h - 1, 0)
        points = [(int(x + px * max_x), int(y + py * max_y)) for px, py in info['points']]
        pygame.draw.rect(surface, TILE_TYPE_INFO['open']['color'], screen_rect)
        pygame.draw.polygon(surface, info['color'], points)


# converts a map file into a tile matrix
class Map:
    def __init__(self, filepath: str):
        self.grid: list[list] = []
        self.rows: int = 0
        self.cols: int = 0
        self.pixel_width: int = 0
        self.pixel_height: int = 0
        self.player1_spawn: tuple[float, float] | None = None
        spawn_count = 0

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
                    spawn_count += 1
                    self.player1_spawn = (
                        col_idx * TILE_SIZE + TILE_SIZE / 2,
                        row_idx * TILE_SIZE + TILE_SIZE / 2,
                    )

                row.append(Tile(tile_type=tile_type, grid_x=col_idx, grid_y=row_idx))
            self.grid.append(row)

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


# camera view that converts between world and screen coordinates
class Camera:
    def __init__(self, viewport_w: int, viewport_h: int):
        self.viewport_w: int = viewport_w
        self.viewport_h: int = viewport_h
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0

    # centers the camera on a world-space point.
    def center_on(self, world_x: float, world_y: float):
        self.offset_x = world_x - self.viewport_w / 2
        self.offset_y = world_y - self.viewport_h / 2

    # returns the currently visible world-space rectangle.
    def get_world_rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.offset_x), int(self.offset_y), self.viewport_w, self.viewport_h)

    # converts a world-space rectangle into screen-space coordinates.
    def world_to_screen(self, world_rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(world_rect.x - int(self.offset_x), world_rect.y - int(self.offset_y), world_rect.width, world_rect.height)


# handles vehicle position, movement, and rendering surface
class Vehicle:
    def __init__(self, start_x: float, start_y: float):
        self.world_x: float = start_x
        self.world_y: float = start_y
        self.vel_x: float = 0.0
        self.vel_y: float = 0.0
        self.input_x: int = 0
        self.input_y: int = 0

        self.surface: pygame.Surface = pygame.Surface((PLAYER_SIZE, PLAYER_SIZE), pygame.SRCALPHA)
        self.surface.fill(COLOR_PLAYER)
        pygame.draw.rect(self.surface, (0, 0, 0), self.surface.get_rect(), 1)

    # returns the world-space rectangle of the player
    def get_bounding_rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.world_x) - PLAYER_SIZE // 2, int(self.world_y) - PLAYER_SIZE // 2, PLAYER_SIZE, PLAYER_SIZE)

    # updates input direction from arrow keys
    def handle_input(self):
        keys = pygame.key.get_pressed()

        self.input_x = int(keys[pygame.K_RIGHT]) - int(keys[pygame.K_LEFT])
        self.input_y = int(keys[pygame.K_DOWN]) - int(keys[pygame.K_UP])

    # steps velocity and position using input acceleration and drag
    def update_physics(self, dt: float):
        accel_x = PLAYER_ACCEL * self.input_x - PLAYER_DRAG * self.vel_x
        accel_y = PLAYER_ACCEL * self.input_y - PLAYER_DRAG * self.vel_y

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

    # draws the player sprite at the given screen rectangle.
    def draw(self, surface: pygame.Surface, screen_rect: pygame.Rect):
        surface.blit(self.surface, screen_rect.topleft)


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

# renders only map tiles that are inside the camera viewport
def render_map(screen: pygame.Surface, game_map: Map, camera: Camera):
    for tile in game_map.get_tiles_in_rect(camera.get_world_rect()):
        tile.draw(screen, camera.world_to_screen(tile.world_rect))

# renders the player through the camera transform
def render_player(screen: pygame.Surface, player: Vehicle, camera: Camera):
    player.draw(screen, camera.world_to_screen(player.get_bounding_rect()))


# =============================================================================
# Game logic
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption("PiKart")
clock = pygame.time.Clock()

game_map = Map(MAP_FILE)
player = Vehicle(*(game_map.player1_spawn))
camera = Camera(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            running = False

    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    player.handle_input()
    player.update_physics(dt)
    player.resolve_collisions(game_map)
    camera.center_on(player.world_x, player.world_y)

    screen.fill(COLOR_BACKGROUND)
    render_map(screen, game_map, camera)
    render_player(screen, player, camera)
    pygame.display.flip()

pygame.quit()
sys.exit()