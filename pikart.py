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

    # TODO: temp
    # finds the first non-wall tile center for player spawn.
    def find_open_cell(self):
        for row in self.grid:
            for tile in row:
                tile_info = TILE_TYPE_INFO.get(tile.tile_type, TILE_TYPE_INFO['open'])
                if not tile_info['is_wall']:
                    return (tile.world_rect.centerx, tile.world_rect.centery)
        return (self.pixel_width / 2, self.pixel_height / 2)


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

    # draws the player sprite at the given screen rectangle.
    def draw(self, surface: pygame.Surface, screen_rect: pygame.Rect):
        surface.blit(self.surface, screen_rect.topleft)


# =============================================================================
# Helpers
# =============================================================================

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))

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
    camera.center_on(player.world_x, player.world_y)

    screen.fill(COLOR_BACKGROUND)
    render_map(screen, game_map, camera)
    render_player(screen, player, camera)
    pygame.display.flip()

pygame.quit()
sys.exit()