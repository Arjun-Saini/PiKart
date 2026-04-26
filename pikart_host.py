import math
import queue
import socket
import sys
import threading

import pygame

from pikart_shared import (
    HOST_IP,
    HOST_PORT,
    VIEWPORT_WIDTH,
    VIEWPORT_HEIGHT,
    FPS,
    TOTAL_LAPS,
    COUNTDOWN_DURATION,
    GO_DISPLAY_DURATION,
    MAX_PHYSICS_DT,
    COLOR_BACKGROUND,
    COLOR_PLAYER,
    COLOR_PLAYER2,
    STATE_MENU,
    STATE_WAITING,
    STATE_MAP_SELECT,
    STATE_GAME,
    STATE_POST_RACE,
    Map,
    Camera,
    Vehicle,
    build_masks,
    build_map_row_rects,
    make_button_rect,
    render_menu,
    render_status_screen,
    render_map_select,
    render_post_race,
    render_center_overlay_message,
    render_map,
    render_hud,
    scan_map_files,
    send_msg,
    recv_msg,
    update_lap_progress,
)

# =============================================================================
# Network threads
# =============================================================================

# Sentinel placed on queues to signal a lost connection.
DISCONNECTED = object()


def flush_queue(q: queue.Queue):
    """Discards all items currently sitting in a queue."""
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


def send_thread(conn: socket.socket, state_q: queue.Queue, input_q: queue.Queue):
    """Drains state_q and sends each packet to the client.

    Blocking send only — never touches recv. Signals input_q with DISCONNECTED
    on failure so the recv thread and main loop both notice the drop.
    """
    try:
        while True:
            payload = state_q.get()
            if payload is DISCONNECTED:
                break
            send_msg(conn, payload)
    except OSError:
        pass
    finally:
        input_q.put(DISCONNECTED)
        try:
            conn.close()
        except OSError:
            pass


def recv_thread(conn: socket.socket, input_q: queue.Queue, state_q: queue.Queue):
    """Reads input packets from the client and puts them on input_q.

    Blocking recv only — never touches send. Signals state_q with DISCONNECTED
    on failure so the send thread unblocks and exits.
    """
    try:
        while True:
            msg = recv_msg(conn)
            input_q.put(msg)
    except OSError:
        pass
    finally:
        input_q.put(DISCONNECTED)
        state_q.put(DISCONNECTED)
        try:
            conn.close()
        except OSError:
            pass


def start_net_threads(conn: socket.socket, input_q: queue.Queue, state_q: queue.Queue):
    """Starts the send and recv background threads for an established connection."""
    threading.Thread(target=send_thread, args=(conn, state_q, input_q), daemon=True).start()
    threading.Thread(target=recv_thread, args=(conn, input_q, state_q), daemon=True).start()


def accept_thread(server_sock: socket.socket, result_q: queue.Queue):
    """Blocks on accept() and puts the resulting socket into result_q."""
    try:
        conn, _ = server_sock.accept()
        result_q.put(conn)
    except OSError:
        result_q.put(None)


# =============================================================================
# Race helpers
# =============================================================================

def make_vehicles(game_map: Map) -> tuple[Vehicle, Vehicle]:
    """Creates player 1 (arrow keys) and player 2 (WASD) vehicles at their spawns."""
    p1 = Vehicle(
        *game_map.player1_spawn,
        throttle_forward_key=pygame.K_UP,
        throttle_back_key=pygame.K_DOWN,
        steer_left_key=pygame.K_LEFT,
        steer_right_key=pygame.K_RIGHT,
        color=COLOR_PLAYER,
    )
    p2 = Vehicle(
        *game_map.player2_spawn,
        throttle_forward_key=pygame.K_w,
        throttle_back_key=pygame.K_s,
        steer_left_key=pygame.K_a,
        steer_right_key=pygame.K_d,
        color=COLOR_PLAYER2,
    )
    return p1, p2


def build_state_packet(
    p1: Vehicle,
    p2: Vehicle,
    countdown: float,
    go_display: float,
    game_state: str,
) -> dict:
    """Serialises the authoritative game state into a packet for the client."""
    return {
        'state': game_state,
        'countdown': round(countdown, 4),
        'go': go_display > 0.0,
        'p1': {
            'x':       round(p1.world_x, 3),
            'y':       round(p1.world_y, 3),
            'heading': round(p1.heading, 5),
            'lap':     p1.max_lap,
            'place':   p1.finish_place,
        },
        'p2': {
            'x':       round(p2.world_x, 3),
            'y':       round(p2.world_y, 3),
            'heading': round(p2.heading, 5),
            'lap':     p2.max_lap,
            'place':   p2.finish_place,
        },
    }


# =============================================================================
# Main
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption('PiKart — Host')
clock = pygame.time.Clock()

hud_font        = pygame.font.SysFont(None, 30)
place_font      = pygame.font.SysFont(None, 54)
countdown_font  = pygame.font.SysFont(None, 110)
title_font      = pygame.font.SysFont(None, 92)
button_font     = pygame.font.SysFont(None, 48)

triangle_masks, player_mask = build_masks()

full_viewport = pygame.Rect(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

# main menu buttons
play_button_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 54)
quit_button_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 + 54)

# map select buttons (rebuilt each time map select is entered)
map_names: list[str] = []
map_row_rects: list[pygame.Rect] = []
map_select_back_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT - 70, width=160, height=60)

# post-race buttons
post_race_again_rect = make_button_rect(VIEWPORT_WIDTH // 2 - 120, VIEWPORT_HEIGHT // 2 + 20, width=200, height=64)
post_race_menu_rect  = make_button_rect(VIEWPORT_WIDTH // 2 + 120, VIEWPORT_HEIGHT // 2 + 20, width=200, height=64)

# game state
current_state: str = STATE_MENU
error_msg: str | None = None

game_map: Map | None = None
map_surface: pygame.Surface | None = None
selected_map_stem: str | None = None
p1: Vehicle | None = None
p2: Vehicle | None = None
camera: Camera | None = None
countdown_remaining: float = 0.0
go_display_remaining: float = 0.0
next_finish_place: int = 1

# local timers (host tracks p1's time; p2's time is tracked on the client)
p1_total_timer: float = 0.0
p1_lap_timer: float = 0.0

# networking
server_sock: socket.socket | None = None
conn_sock: socket.socket | None = None
accept_result_q: queue.Queue = queue.Queue()
input_q: queue.Queue = queue.Queue()
state_q: queue.Queue = queue.Queue()
last_client_input: dict = {'throttle': 0, 'steer': 0}


def open_server_socket():
    """Opens the TCP server socket and starts the background accept thread."""
    global server_sock
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST_IP, HOST_PORT))
    server_sock.listen(1)
    flush_queue(accept_result_q)
    t = threading.Thread(target=accept_thread, args=(server_sock, accept_result_q), daemon=True)
    t.start()


def start_race(map_stem: str):
    """Loads the map and initialises all race state for a fresh start."""
    global game_map, map_surface, p1, p2, camera
    global countdown_remaining, go_display_remaining, next_finish_place
    global p1_total_timer, p1_lap_timer
    game_map = Map(map_stem + '.txt')
    map_surface = game_map.build_surface()
    p1, p2 = make_vehicles(game_map)
    camera = Camera(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
    camera.center_on(p1.world_x, p1.world_y)
    camera.set_heading(p1.heading)
    countdown_remaining = COUNTDOWN_DURATION
    go_display_remaining = 0.0
    next_finish_place = 1
    p1_total_timer = 0.0
    p1_lap_timer = 0.0


def disconnect(reason: str | None):
    """Cleans up the connection and returns to the menu."""
    global conn_sock, current_state, error_msg
    if conn_sock is not None:
        try:
            conn_sock.close()
        except OSError:
            pass
        conn_sock = None
    # Unblock the send thread (it blocks on state_q.get()) then flush both queues.
    state_q.put(DISCONNECTED)
    flush_queue(input_q)
    error_msg = reason
    current_state = STATE_MENU


# =============================================================================
# Main loop
# =============================================================================

running = True
while running:
    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    # ---- Check for completed accept() ----
    if current_state == STATE_WAITING:
        try:
            result = accept_result_q.get_nowait()
            if result is None:
                disconnect('Failed to accept connection.')
            else:
                conn_sock = result
                flush_queue(input_q)
                flush_queue(state_q)
                start_net_threads(conn_sock, input_q, state_q)
                # Notify the client that the connection is established so it
                # can show "Player 1 is selecting a map..." before any map
                # packet or state packet arrives.
                state_q.put({'connected': True})
                map_names = scan_map_files()
                map_row_rects = build_map_row_rects(len(map_names))
                current_state = STATE_MAP_SELECT
        except queue.Empty:
            pass

    # ---- Check for disconnect sentinel from net thread ----
    if current_state in (STATE_GAME, STATE_POST_RACE, STATE_MAP_SELECT):
        try:
            item = input_q.get_nowait()
            if item is DISCONNECTED:
                disconnect('Connection lost.')
            else:
                last_client_input = item
        except queue.Empty:
            pass

    # ---- Pygame events ----
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if current_state in (STATE_GAME, STATE_POST_RACE, STATE_MAP_SELECT, STATE_WAITING):
                disconnect(None)
                error_msg = None
                if server_sock is not None:
                    try:
                        server_sock.close()
                    except OSError:
                        pass
                    server_sock = None
            else:
                running = False

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos

            if current_state == STATE_MENU:
                if play_button_rect.collidepoint(pos):
                    error_msg = None
                    open_server_socket()
                    current_state = STATE_WAITING
                elif quit_button_rect.collidepoint(pos):
                    running = False

            elif current_state == STATE_MAP_SELECT:
                if map_select_back_rect.collidepoint(pos):
                    # Back to waiting — keep the connection alive, client stays
                    # on "Player 1 is selecting a map...".
                    current_state = STATE_WAITING
                else:
                    for i, rect in enumerate(map_row_rects):
                        if rect.collidepoint(pos):
                            selected_map_stem = map_names[i]
                            send_msg(conn_sock, {'map': selected_map_stem})
                            start_race(selected_map_stem)
                            current_state = STATE_GAME
                            break

            elif current_state == STATE_POST_RACE:
                if post_race_again_rect.collidepoint(pos):
                    # Notify client to wait for a new map selection, then go
                    # back to map select on the host side.
                    state_q.put({'replay': True})
                    map_names = scan_map_files()
                    map_row_rects = build_map_row_rects(len(map_names))
                    current_state = STATE_MAP_SELECT
                elif post_race_menu_rect.collidepoint(pos):
                    disconnect(None)
                    error_msg = None
                    if server_sock is not None:
                        try:
                            server_sock.close()
                        except OSError:
                            pass
                        server_sock = None

    # =========================================================================
    # STATE: MENU
    # =========================================================================
    if current_state == STATE_MENU:
        render_menu(screen, play_button_rect, quit_button_rect, title_font, button_font, error_msg)
        pygame.display.flip()
        continue

    # =========================================================================
    # STATE: WAITING — server socket open, waiting for client to connect
    # =========================================================================
    if current_state == STATE_WAITING:
        render_status_screen(screen, title_font, button_font, 'Waiting for player 2...')
        pygame.display.flip()
        continue

    # =========================================================================
    # STATE: MAP SELECT — connected, host picks the map
    # =========================================================================
    if current_state == STATE_MAP_SELECT:
        render_map_select(screen, map_names, map_row_rects, title_font, button_font, map_select_back_rect)
        pygame.display.flip()
        continue

    # =========================================================================
    # STATE: POST RACE
    # =========================================================================
    if current_state == STATE_POST_RACE:
        screen.fill(COLOR_BACKGROUND)
        render_map(screen, map_surface, camera, full_viewport, overlay_vehicles=[p2])
        p1.draw(screen, full_viewport.centerx, full_viewport.centery)
        render_hud(screen, full_viewport, p1.max_lap, TOTAL_LAPS, p1_total_timer, p1_lap_timer, p1.finish_place, hud_font, place_font)
        render_post_race(screen, post_race_again_rect, post_race_menu_rect, title_font, button_font)
        pygame.display.flip()

        # Keep broadcasting post-race state so the client stays in sync.
        state_packet = build_state_packet(p1, p2, 0.0, 0.0, STATE_POST_RACE)
        state_q.put(state_packet)
        continue

    # =========================================================================
    # STATE: GAME
    # =========================================================================

    # Advance countdown timer (host-authoritative).
    if countdown_remaining > 0.0:
        countdown_remaining = max(0.0, countdown_remaining - dt)
        if countdown_remaining == 0.0:
            go_display_remaining = GO_DISPLAY_DURATION

    if go_display_remaining > 0.0:
        go_display_remaining = max(0.0, go_display_remaining - dt)

    input_enabled = countdown_remaining <= 0.0

    if input_enabled:
        # Apply host's own keys to p1.
        p1.handle_input()

        # Apply the most recent client input to p2.
        p2.throttle_input = int(last_client_input.get('throttle', 0))
        p2.steer_input    = int(last_client_input.get('steer', 0))

        p1.update_physics(dt)
        p2.update_physics(dt)

        p1_finished = update_lap_progress(p1, game_map)
        p2_finished = update_lap_progress(p2, game_map)

        if p1_finished:
            p1.finish_place = next_finish_place
            next_finish_place += 1
        if p2_finished:
            p2.finish_place = next_finish_place
            next_finish_place += 1

        # Host tracks p1's timers locally; p2's timers live on the client.
        if not p1.race_finished:
            p1_total_timer += dt
            p1_lap_timer   += dt

        p1.resolve_collisions(game_map, triangle_masks, player_mask)
        p2.resolve_collisions(game_map, triangle_masks, player_mask)

        if p1.race_finished and p2.race_finished:
            current_state = STATE_POST_RACE

    # Update camera to follow p1.
    camera.center_on(p1.world_x, p1.world_y)
    camera.set_heading(p1.heading)

    # Render.
    screen.fill(COLOR_BACKGROUND)
    render_map(screen, map_surface, camera, full_viewport, overlay_vehicles=[p2])
    p1.draw(screen, full_viewport.centerx, full_viewport.centery)
    render_hud(screen, full_viewport, p1.max_lap, TOTAL_LAPS, p1_total_timer, p1_lap_timer, p1.finish_place, hud_font, place_font)

    if countdown_remaining > 0.0:
        render_center_overlay_message(screen, str(int(math.ceil(countdown_remaining))), countdown_font)
    elif go_display_remaining > 0.0:
        render_center_overlay_message(screen, 'GO!', countdown_font)

    pygame.display.flip()

    # Send state packet to client.
    state_packet = build_state_packet(p1, p2, countdown_remaining, go_display_remaining, STATE_GAME)
    state_q.put(state_packet)

pygame.quit()
sys.exit()