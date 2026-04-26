import math
import queue
import socket
import sys
import threading
import time

import pygame

from pikart_shared import (
    HOST_IP,
    HOST_PORT,
    VIEWPORT_WIDTH,
    VIEWPORT_HEIGHT,
    FPS,
    TOTAL_LAPS,
    MAX_PHYSICS_DT,
    COLOR_BACKGROUND,
    COLOR_PLAYER,
    COLOR_PLAYER2,
    STATE_MENU,
    STATE_WAITING,
    STATE_GAME,
    STATE_POST_RACE,
    Map,
    Camera,
    Vehicle,
    build_masks,
    make_button_rect,
    render_menu,
    render_status_screen,
    render_post_race,
    render_center_overlay_message,
    render_map,
    render_hud,
    send_msg,
    recv_msg,
)

# =============================================================================
# Network thread
# =============================================================================

DISCONNECTED = object()

CONNECT_RETRY_INTERVAL = 0.5


def flush_queue(q: queue.Queue):
    """Discards all items currently sitting in a queue."""
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


def connect_thread(result_q: queue.Queue):
    """Repeatedly attempts to connect to the host, retrying until success or
    a DISCONNECTED sentinel is found in result_q (injected to cancel).
    Places the connected socket into result_q on success.
    """
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((HOST_IP, HOST_PORT))
            result_q.put(sock)
            return
        except OSError:
            sock.close()
            time.sleep(CONNECT_RETRY_INTERVAL)
            try:
                item = result_q.get_nowait()
                if item is DISCONNECTED:
                    return
                result_q.put(item)
            except queue.Empty:
                pass


def send_thread(sock: socket.socket, input_q: queue.Queue, state_q: queue.Queue):
    """Drains input_q and sends each packet to the host.

    Blocking send only. Signals state_q with DISCONNECTED on failure.
    """
    try:
        while True:
            payload = input_q.get()
            if payload is DISCONNECTED:
                break
            send_msg(sock, payload)
    except OSError:
        pass
    finally:
        state_q.put(DISCONNECTED)
        try:
            sock.close()
        except OSError:
            pass


def recv_thread(sock: socket.socket, state_q: queue.Queue, input_q: queue.Queue):
    """Reads state packets from the host and puts them on state_q.

    Blocking recv only. Signals input_q with DISCONNECTED on failure so the
    send thread unblocks and exits.
    """
    try:
        while True:
            msg = recv_msg(sock)
            state_q.put(msg)
    except OSError:
        pass
    finally:
        state_q.put(DISCONNECTED)
        input_q.put(DISCONNECTED)
        try:
            sock.close()
        except OSError:
            pass


def start_net_threads(sock: socket.socket, input_q: queue.Queue, state_q: queue.Queue):
    """Starts the send and recv background threads for an established connection."""
    threading.Thread(target=send_thread, args=(sock, input_q, state_q), daemon=True).start()
    threading.Thread(target=recv_thread, args=(sock, state_q, input_q), daemon=True).start()


# =============================================================================
# Mirror vehicle (position-only, no physics)
# =============================================================================

class MirrorVehicle:
    """A display-only vehicle whose position is updated from network state packets."""

    def __init__(self, color: tuple[int, int, int]):
        self.world_x: float = 0.0
        self.world_y: float = 0.0
        self.heading: float = 0.0
        self.max_lap: int = 1
        self.finish_place: int | None = None
        self.color: tuple[int, int, int] = color

    def apply_state(self, data: dict):
        """Updates position, heading, lap, and finish place from a state sub-dict."""
        self.world_x     = float(data['x'])
        self.world_y     = float(data['y'])
        self.heading     = float(data['heading'])
        self.max_lap     = int(data['lap'])
        self.finish_place = data.get('place')

    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        from pikart_shared import PLAYER_SIZE
        screen_rect = pygame.Rect(
            screen_x - PLAYER_SIZE // 2,
            screen_y - PLAYER_SIZE // 2,
            PLAYER_SIZE,
            PLAYER_SIZE,
        )
        pygame.draw.rect(surface, self.color, screen_rect)
        pygame.draw.rect(surface, (0, 0, 0), screen_rect, 1)


# =============================================================================
# Main
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption('PiKart — Client')
clock = pygame.time.Clock()

hud_font       = pygame.font.SysFont(None, 30)
place_font     = pygame.font.SysFont(None, 54)
countdown_font = pygame.font.SysFont(None, 110)
title_font     = pygame.font.SysFont(None, 92)
button_font    = pygame.font.SysFont(None, 48)

triangle_masks, player_mask = build_masks()

full_viewport = pygame.Rect(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

# main menu buttons
play_button_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 54)
quit_button_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 + 54)

# post-race buttons (layout mirrors the host)
post_race_again_rect = make_button_rect(VIEWPORT_WIDTH // 2 - 120, VIEWPORT_HEIGHT // 2 + 20, width=200, height=64)
post_race_menu_rect  = make_button_rect(VIEWPORT_WIDTH // 2 + 120, VIEWPORT_HEIGHT // 2 + 20, width=200, height=64)

# game state
current_state: str = STATE_MENU
error_msg: str | None = None

# waiting_msg is shown during STATE_WAITING and changes once the connection
# is established and we're waiting for the host to pick a map.
waiting_msg: str = 'Connecting...'

game_map: Map | None = None
map_surface: pygame.Surface | None = None

# p2 is the local (client) player; p1 is the host player shown as overlay.
p2_mirror = MirrorVehicle(color=COLOR_PLAYER2)
p1_mirror = MirrorVehicle(color=COLOR_PLAYER)
camera = Camera(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

# Client tracks its own (p2) timers locally.
p2_total_timer: float = 0.0
p2_lap_timer: float = 0.0
p2_prev_lap: int = 1
p2_race_finished: bool = False

# Latest host-authoritative values used for HUD and overlay rendering.
latest_countdown: float = 0.0
latest_go: bool = False
latest_state_name: str = STATE_MENU

# Networking queues
connect_result_q: queue.Queue = queue.Queue()
input_q: queue.Queue = queue.Queue()
state_q: queue.Queue = queue.Queue()
sock: socket.socket | None = None

# Input keys for the client player (p2 = WASD)
KEY_FORWARD  = pygame.K_w
KEY_BACK     = pygame.K_s
KEY_LEFT     = pygame.K_a
KEY_RIGHT    = pygame.K_d


def disconnect(reason: str | None):
    """Closes the socket and returns to the menu."""
    global sock, current_state, error_msg, waiting_msg
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
        sock = None
    # Unblock the send thread then flush both queues.
    input_q.put(DISCONNECTED)
    flush_queue(state_q)
    error_msg = reason
    waiting_msg = 'Connecting...'
    current_state = STATE_MENU


def reset_local_timers():
    """Resets client-side p2 timer state for a new race."""
    global p2_total_timer, p2_lap_timer, p2_prev_lap, p2_race_finished
    p2_total_timer   = 0.0
    p2_lap_timer     = 0.0
    p2_prev_lap      = 1
    p2_race_finished = False


# =============================================================================
# Main loop
# =============================================================================

running = True
while running:
    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    # ---- Check for completed connect() ----
    if current_state == STATE_WAITING and sock is None:
        try:
            result = connect_result_q.get_nowait()
            if result is DISCONNECTED or result is None:
                disconnect('Could not connect to host.')
            else:
                sock = result
                sock.settimeout(None)
                flush_queue(input_q)
                flush_queue(state_q)
                # waiting_msg stays as 'Connecting...' until the map handshake
                # arrives, at which point it flips to the map-select message.
                start_net_threads(sock, input_q, state_q)
        except queue.Empty:
            pass

    # ---- Drain incoming packets ----
    if current_state in (STATE_WAITING, STATE_GAME, STATE_POST_RACE):
        try:
            while True:
                pkt = state_q.get_nowait()
                if pkt is DISCONNECTED:
                    disconnect('Connection lost.')
                    break

                # Replay packet — host is going back to map select; client
                # returns to STATE_WAITING with the map-select message.
                if 'replay' in pkt:
                    waiting_msg = 'Player 1 is selecting a map...'
                    current_state = STATE_WAITING
                    continue

                # Connection confirmation — flip the waiting message so the
                # player sees "Player 1 is selecting a map..." immediately.
                if 'connected' in pkt and current_state == STATE_WAITING:
                    waiting_msg = 'Player 1 is selecting a map...'
                    continue

                # Map handshake packet — load map, stay in STATE_WAITING until
                # the first game state packet arrives.
                if 'map' in pkt and current_state == STATE_WAITING and sock is not None:
                    map_stem = pkt['map']
                    game_map = Map(map_stem + '.txt')
                    map_surface = game_map.build_surface()
                    reset_local_timers()
                    p2_mirror.apply_state({
                        'x': game_map.player2_spawn[0],
                        'y': game_map.player2_spawn[1],
                        'heading': 0.0, 'lap': 1, 'place': None,
                    })
                    p1_mirror.apply_state({
                        'x': game_map.player1_spawn[0],
                        'y': game_map.player1_spawn[1],
                        'heading': 0.0, 'lap': 1, 'place': None,
                    })
                    camera.center_on(p2_mirror.world_x, p2_mirror.world_y)
                    camera.set_heading(p2_mirror.heading)
                    continue

                # Regular state packet.
                if 'state' in pkt:
                    latest_state_name = pkt.get('state', STATE_GAME)
                    latest_countdown  = float(pkt.get('countdown', 0.0))
                    latest_go         = bool(pkt.get('go', False))

                    p1_mirror.apply_state(pkt['p1'])
                    p2_mirror.apply_state(pkt['p2'])

                    if latest_state_name == STATE_GAME and current_state == STATE_WAITING:
                        current_state = STATE_GAME
                    elif latest_state_name == STATE_POST_RACE and current_state != STATE_POST_RACE:
                        p2_race_finished = True
                        current_state = STATE_POST_RACE

        except queue.Empty:
            pass

    # ---- Advance local p2 timers ----
    if current_state == STATE_GAME and latest_countdown <= 0.0 and not p2_race_finished:
        if p2_mirror.max_lap > p2_prev_lap:
            p2_lap_timer = 0.0
            p2_prev_lap  = p2_mirror.max_lap
        if p2_mirror.finish_place is not None:
            p2_race_finished = True
        if not p2_race_finished:
            p2_total_timer += dt
            p2_lap_timer   += dt

    # ---- Sample and send input ----
    if current_state == STATE_GAME and latest_countdown <= 0.0 and not p2_race_finished:
        keys = pygame.key.get_pressed()
        throttle = int(keys[KEY_FORWARD]) - int(keys[KEY_BACK])
        steer    = int(keys[KEY_RIGHT])   - int(keys[KEY_LEFT])
        input_q.put({'throttle': throttle, 'steer': steer})

    # ---- Pygame events ----
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if current_state in (STATE_GAME, STATE_POST_RACE, STATE_WAITING):
                if current_state == STATE_WAITING and sock is None:
                    connect_result_q.put(DISCONNECTED)
                disconnect(None)
                error_msg = None
            else:
                running = False

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos

            if current_state == STATE_MENU:
                if play_button_rect.collidepoint(pos):
                    error_msg = None
                    waiting_msg = 'Connecting...'
                    flush_queue(connect_result_q)
                    flush_queue(input_q)
                    flush_queue(state_q)
                    t = threading.Thread(target=connect_thread, args=(connect_result_q,), daemon=True)
                    t.start()
                    current_state = STATE_WAITING
                elif quit_button_rect.collidepoint(pos):
                    running = False

            elif current_state == STATE_POST_RACE:
                if post_race_menu_rect.collidepoint(pos):
                    disconnect(None)
                    error_msg = None

    # =========================================================================
    # Rendering
    # =========================================================================

    if current_state == STATE_MENU:
        render_menu(screen, play_button_rect, quit_button_rect, title_font, button_font, error_msg)
        pygame.display.flip()
        continue

    if current_state == STATE_WAITING:
        render_status_screen(screen, title_font, button_font, waiting_msg)
        pygame.display.flip()
        continue

    # Update camera to follow p2 (the local client player).
    camera.center_on(p2_mirror.world_x, p2_mirror.world_y)
    camera.set_heading(p2_mirror.heading)

    screen.fill(COLOR_BACKGROUND)
    render_map(screen, map_surface, camera, full_viewport, overlay_vehicles=[p1_mirror])
    p2_mirror.draw(screen, full_viewport.centerx, full_viewport.centery)
    render_hud(
        screen, full_viewport,
        p2_mirror.max_lap, TOTAL_LAPS,
        p2_total_timer, p2_lap_timer,
        p2_mirror.finish_place,
        hud_font, place_font,
    )

    if latest_countdown > 0.0:
        render_center_overlay_message(screen, str(int(math.ceil(latest_countdown))), countdown_font)
    elif latest_go:
        render_center_overlay_message(screen, 'GO!', countdown_font)

    if current_state == STATE_POST_RACE:
        render_post_race(screen, post_race_again_rect, post_race_menu_rect, title_font, button_font)

    pygame.display.flip()

pygame.quit()
sys.exit()