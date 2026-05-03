from __future__ import annotations
import math
import os
import queue
import socket
import sys
import threading
import time

import pygame

from pikart_shared import (
    HOST_PORT, VIEWPORT_WIDTH, VIEWPORT_HEIGHT, FPS,
    TOTAL_LAPS, MAX_PHYSICS_DT, COLOR_BACKGROUND, COLOR_PLAYER, COLOR_PLAYER2,
    COLOR_TRACK_P1, COLOR_TRACK_P2,
    COLOR_ERROR, TILE_TYPE_INFO, TILE_SIZE, PLAYER_SIZE,
    STATE_MENU, STATE_WAITING, STATE_GAME, STATE_POST_RACE,
    Map, Camera, Vehicle, Shell, PowerupSpawner,
    _draw_player_sprite_static,
    POWERUP_SIZE_GROW, POWERUP_SIZE_SHRINK, POWERUP_GREEN_SHELL, POWERUP_RED_SHELL,
    POWERUP_SIZE_SCALE, POWERUP_SIZE_DURATION, SHELL_SIZE,
    BOOST_TILE_DELTA, SLOWDOWN_TILE_DELTA, TRACK_HISTORY_INTERVAL,
    DISCONNECTED, flush_queue, net_send_thread, net_recv_thread,
    _blit_c, _btn, make_button_rect,
    render_menu, render_status_screen, render_post_race,
    render_center_overlay_message, render_map, render_hud,
    draw_track_history,
    send_msg, recv_msg, aabb_mtv,
    joystick_init, joystick_stop, joystick_throttle, joystick_steer,
    joystick_consume_press, joystick_btn, joystick_menu_x, joystick_menu_y, JS_SW,
    motor_init, motor_stop, motor_rumble, motor_update,
)

DEBUG   = 'debug' in sys.argv
HOST_IP = '127.0.0.1' if DEBUG else '192.168.50.1'
if not DEBUG:
    joystick_init()
    motor_init()
def log(msg: str):
    print(f"[HOST {time.strftime('%H:%M:%S')}] {msg}", flush=True)

# =============================================================================
# Host-only constants
# =============================================================================

STATE_MAP_SELECT     = 'map_select'
COUNTDOWN_DURATION   = 3.0
GO_DISPLAY_DURATION  = 0.75

# =============================================================================
# Host-only game logic
# =============================================================================

def resolve_vehicle_collision(a: Vehicle, b: Vehicle) -> bool:
    """Resolve an elastic collision between two vehicles.

    Returns True if the vehicles were overlapping and a correction was applied.
    """
    contact = aabb_mtv(a.get_bounding_rect(), b.get_bounding_rect())
    if contact is None:
        return False
    nx, ny, pen = contact
    push = (pen + 0.05) * 0.5
    a.world_x += nx * push;  a.world_y += ny * push
    b.world_x -= nx * push;  b.world_y -= ny * push
    van = a.vel_x * nx + a.vel_y * ny
    vbn = b.vel_x * nx + b.vel_y * ny
    if van - vbn >= 0.0:
        return True
    a.vel_x += (vbn - van) * nx;  a.vel_y += (vbn - van) * ny
    b.vel_x += (van - vbn) * nx;  b.vel_y += (van - vbn) * ny
    return True


def update_lap_progress(player: Vehicle, game_map: Map) -> bool:
    if player.race_finished:
        return False
    fx = game_map.finish_line_x
    dx = player.world_x - player.prev_world_x
    crossed = ((player.prev_world_x < fx <= player.world_x) or
               (player.prev_world_x > fx >= player.world_x))
    y_cross = player.world_y
    if abs(dx) > 1e-6:
        t = max(0.0, min((fx - player.prev_world_x) / dx, 1.0))
        y_cross = player.prev_world_y + t * (player.world_y - player.prev_world_y)
    if not (game_map.finish_line_y_min <= y_cross <= game_map.finish_line_y_max and crossed):
        return False
    if dx > 0.0:
        if player.ignore_first_forward_cross:
            player.ignore_first_forward_cross = False
            return False
        if player.curr_lap >= TOTAL_LAPS:
            player.race_finished = True
            return True
        player.curr_lap += 1
        player.max_lap = max(player.max_lap, player.curr_lap)
    elif dx < 0.0:
        player.curr_lap = max(1, player.curr_lap - 1)
    return False


def apply_passive_tile_effects(vehicle: Vehicle, game_map: Map):
    rect = vehicle.get_bounding_rect()
    on_p1 = on_p2 = False
    for tile in game_map.get_tiles_in_rect(rect):
        if   tile.tile_type == 'powerup1' and rect.colliderect(tile.world_rect): on_p1 = True
        elif tile.tile_type == 'powerup2' and rect.colliderect(tile.world_rect): on_p2 = True
    if on_p1 and not vehicle.on_powerup1:
        vehicle.vel_x += math.cos(vehicle.heading) * BOOST_TILE_DELTA
        vehicle.vel_y += math.sin(vehicle.heading) * BOOST_TILE_DELTA
    if on_p2 and not vehicle.on_powerup2:
        speed = math.hypot(vehicle.vel_x, vehicle.vel_y)
        if speed > 1e-3:
            f = max(0.0, speed - SLOWDOWN_TILE_DELTA) / speed
            vehicle.vel_x *= f
            vehicle.vel_y *= f
    vehicle.on_powerup1 = on_p1
    vehicle.on_powerup2 = on_p2


# add new powerup types with an elif branch here
def activate_consumable(vehicle: Vehicle, opponent: Vehicle, shells: list, owner_index: int):
    ptype = vehicle.stored_powerup
    if ptype is None:
        return
    vehicle.stored_powerup = None
    if ptype == POWERUP_SIZE_GROW:
        vehicle.size_multiplier = POWERUP_SIZE_SCALE
        vehicle.size_timer      = POWERUP_SIZE_DURATION
    elif ptype == POWERUP_SIZE_SHRINK:
        vehicle.size_multiplier = 1.0 / POWERUP_SIZE_SCALE
        vehicle.size_timer      = POWERUP_SIZE_DURATION
    elif ptype in (POWERUP_GREEN_SHELL, POWERUP_RED_SHELL):
        is_green = ptype == POWERUP_GREEN_SHELL
        h = (vehicle.heading + math.pi) % (2*math.pi) if is_green else vehicle.heading
        shells.append(Shell(ptype,
                            vehicle.world_x + math.cos(h) * (PLAYER_SIZE + SHELL_SIZE),
                            vehicle.world_y + math.sin(h) * (PLAYER_SIZE + SHELL_SIZE),
                            h, owner_index))

# =============================================================================
# Host-only render
# =============================================================================

def render_map_select(screen: pygame.Surface, map_names: list[str], map_row_rects: list[pygame.Rect],
                      title_font: pygame.font.Font, button_font: pygame.font.Font, selected_idx: int = 0):
    screen.fill((25, 25, 25))
    _blit_c(screen, title_font.render('Select Map', True, (255,255,255)), (VIEWPORT_WIDTH//2, 20))
    for i, (name, rect) in enumerate(zip(map_names, map_row_rects)):
        _btn(screen, rect, name, button_font, selected_idx == i,
             bg=(50,50,80), bg_h=(80,80,130), fg=(255,255,255), border=(120,120,180))
    if not map_names:
        _blit_c(screen, button_font.render('No .txt maps found.', True, COLOR_ERROR),
                (VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2))


def scan_map_files() -> list[str]:
    return sorted(os.path.splitext(n)[0] for n in os.listdir('.') if n.lower().endswith('.txt') and os.path.isfile(n))


def build_map_row_rects(count: int) -> list[pygame.Rect]:
    return [make_button_rect(VIEWPORT_WIDTH//2, 60 + i*34, width=180, height=26) for i in range(count)]


def build_masks() -> tuple[dict, pygame.mask.Mask]:
    def tri_mask(pts):
        surf = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
        m = TILE_SIZE - 1
        pygame.draw.polygon(surf, (255,255,255), [(int(px*m), int(py*m)) for px, py in pts])
        return pygame.mask.from_surface(surf)
    tri_masks = {t: tri_mask(info['points']) for t, info in TILE_TYPE_INFO.items() if info.get('shape') == 'tri'}
    surf = pygame.Surface((PLAYER_SIZE, PLAYER_SIZE), pygame.SRCALPHA)
    pygame.draw.rect(surf, (255,255,255), surf.get_rect())
    return tri_masks, pygame.mask.from_surface(surf)

# =============================================================================
# Network
# =============================================================================

def start_net_threads(conn: socket.socket, input_q: queue.Queue, state_q: queue.Queue):
    log("starting net threads")
    threading.Thread(target=net_send_thread, args=(conn, state_q, input_q, log), daemon=True).start()
    threading.Thread(target=net_recv_thread, args=(conn, input_q, input_q, state_q, log), daemon=True).start()


def accept_thread(server_sock: socket.socket, result_q: queue.Queue):
    log("accept_thread blocking")
    try:
        conn, addr = server_sock.accept()
        log(f"accepted from {addr}")
        result_q.put(conn)
    except OSError as e:
        log(f"accept OSError: {e}")
        result_q.put(None)

# =============================================================================
# Main
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption('PiKart — Host')
clock = pygame.time.Clock()

hud_font       = pygame.font.SysFont(None, 14)
place_font     = pygame.font.SysFont(None, 26)
countdown_font = pygame.font.SysFont(None, 52)
title_font     = pygame.font.SysFont(None, 44)
button_font    = pygame.font.SysFont(None, 22)

tri_masks, player_mask = build_masks()
full_viewport = pygame.Rect(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

play_button_rect     = make_button_rect(VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2 - 27)
quit_button_rect     = make_button_rect(VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2 + 27)
post_race_again_rect = make_button_rect(VIEWPORT_WIDTH//2 - 60, VIEWPORT_HEIGHT//2 + 10, width=100, height=30)
post_race_menu_rect  = make_button_rect(VIEWPORT_WIDTH//2 + 60, VIEWPORT_HEIGHT//2 + 10, width=100, height=30)

current_state = STATE_MENU
error_msg: str | None = None
menu_sel     = 0   # 0=Play, 1=Quit
map_sel      = 0   # index into map_names
post_sel     = 0   # 0=Play Again, 1=Exit to Menu

game_map: Map | None               = None
map_surface: pygame.Surface | None = None
p1: Vehicle | None = None
p2: Vehicle | None = None
camera: Camera | None = None
map_names:     list[str]         = []
map_row_rects: list[pygame.Rect] = []

countdown_remaining  = 0.0
go_display_remaining = 0.0
next_finish_place    = 1
shells:   list[Shell]          = []
spawners: list[PowerupSpawner] = []
p1_total_timer = 0.0
p1_lap_timer   = 0.0
p1_prev_lap    = 1

server_sock: socket.socket | None = None
conn_sock:   socket.socket | None = None
accept_result_q = queue.Queue()
input_q         = queue.Queue()
state_q         = queue.Queue()
last_client_input = {'throttle': 0, 'steer': 0, 'activate': False}
p2_prev_activate = False
p2_collided    = False
p2_shell_hit   = False


def open_server_socket():
    global server_sock
    log(f"opening server socket on {HOST_IP}:{HOST_PORT}")
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST_IP, HOST_PORT))
    server_sock.listen(1)
    flush_queue(accept_result_q)
    threading.Thread(target=accept_thread, args=(server_sock, accept_result_q), daemon=True).start()


def start_race(map_stem: str):
    global game_map, map_surface, p1, p2, camera
    global countdown_remaining, go_display_remaining, next_finish_place
    global p1_total_timer, p1_lap_timer, shells, spawners, p1_prev_lap
    game_map    = Map(map_stem + '.txt')
    map_surface = game_map.build_surface()
    p1 = Vehicle(*game_map.player1_spawn,
                 throttle_forward_key=pygame.K_UP,    throttle_back_key=pygame.K_DOWN,
                 steer_left_key=pygame.K_LEFT,        steer_right_key=pygame.K_RIGHT,
                 color=COLOR_PLAYER)
    p2 = Vehicle(*game_map.player2_spawn,
                 throttle_forward_key=pygame.K_w,     throttle_back_key=pygame.K_s,
                 steer_left_key=pygame.K_a,           steer_right_key=pygame.K_d,
                 color=COLOR_PLAYER2)
    camera = Camera(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
    camera.center_on(p1.world_x, p1.world_y)
    camera.heading       = p1.heading
    countdown_remaining  = COUNTDOWN_DURATION
    go_display_remaining = 0.0
    next_finish_place    = 1
    p1_total_timer = p1_lap_timer = 0.0
    p1_prev_lap  = 1
    shells   = []
    spawners = [PowerupSpawner(wx, wy) for wx, wy in game_map.spawner_positions]
    global p2_collided, p2_shell_hit
    p2_collided = p2_shell_hit = False


def disconnect(reason: str | None):
    global conn_sock, current_state, error_msg
    log(f"disconnect: {reason!r}")
    if conn_sock is not None:
        try: conn_sock.close()
        except OSError: pass
        conn_sock = None
    state_q.put(DISCONNECTED)
    flush_queue(input_q)
    error_msg     = reason
    current_state = STATE_MENU


def close_server():
    global server_sock
    if server_sock is not None:
        try: server_sock.close()
        except OSError: pass
        server_sock = None


def build_state_packet(game_state: str, countdown: float, go_display: float, track: bool = False) -> dict:
    return {
        'state': game_state, 'countdown': round(countdown, 4), 'go': go_display > 0.0,
        'track': track,
        'p1': {'x': round(p1.world_x, 3), 'y': round(p1.world_y, 3),
               'heading': round(p1.heading, 5), 'lap': p1.max_lap,
               'place': p1.finish_place, 'powerup': p1.stored_powerup,
               'size': round(p1.size_multiplier, 4)},
        'p2': {'x': round(p2.world_x, 3), 'y': round(p2.world_y, 3),
               'heading': round(p2.heading, 5), 'lap': p2.max_lap,
               'place': p2.finish_place, 'powerup': p2.stored_powerup,
               'size': round(p2.size_multiplier, 4)},
        'shells':        [sh.to_dict() for sh in shells],
        'spawners':      [sp.to_dict() for sp in spawners],
        'p2_collided':   p2_collided,
        'p2_shell_hit':  p2_shell_hit,
    }

# =============================================================================
# Main loop
# =============================================================================

running = True
while running:
    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    if current_state == STATE_WAITING:
        try:
            result = accept_result_q.get_nowait()
            if result is None:
                disconnect('Failed to accept connection.')
            else:
                log("accepted, starting net threads")
                conn_sock = result
                flush_queue(input_q)
                flush_queue(state_q)
                start_net_threads(conn_sock, input_q, state_q)
                state_q.put({'connected': True})
                map_names     = scan_map_files()
                map_row_rects = build_map_row_rects(len(map_names))
                current_state = STATE_MAP_SELECT
        except queue.Empty:
            pass

    if current_state in (STATE_GAME, STATE_POST_RACE, STATE_MAP_SELECT):
        try:
            item = input_q.get_nowait()
            if item is DISCONNECTED:
                log("DISCONNECTED from input_q")
                disconnect('Connection lost.')
            else:
                last_client_input = item
        except queue.Empty:
            pass

    # joystick menu navigation (skipped in debug mode since joystick not initialised)
    if not DEBUG and current_state in (STATE_MENU, STATE_MAP_SELECT, STATE_POST_RACE):
        my = -joystick_menu_y()
        mx = joystick_menu_x()
        if current_state == STATE_MENU and my != 0:
            menu_sel = 1 - menu_sel
        elif current_state == STATE_MAP_SELECT and my != 0:
            map_sel = (map_sel + my) % max(1, len(map_names))
        elif current_state == STATE_POST_RACE and mx != 0:
            post_sel = 1 - post_sel
        if joystick_consume_press():
            if current_state == STATE_MENU:
                if menu_sel == 0:
                    error_msg = None; open_server_socket(); current_state = STATE_WAITING
                else:
                    running = False
            elif current_state == STATE_MAP_SELECT and map_names:
                send_msg(conn_sock, {'map': map_names[map_sel]})
                start_race(map_names[map_sel]); current_state = STATE_GAME
            elif current_state == STATE_POST_RACE:
                if post_sel == 0:
                    state_q.put({'replay': True})
                    map_names = scan_map_files(); map_row_rects = build_map_row_rects(len(map_names))
                    map_sel = 0; current_state = STATE_MAP_SELECT
                else:
                    disconnect(None); error_msg = None; close_server()

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            key = event.key
            if key == pygame.K_ESCAPE:
                if current_state in (STATE_GAME, STATE_POST_RACE, STATE_MAP_SELECT, STATE_WAITING):
                    disconnect(None); error_msg = None; close_server()
                else:
                    running = False

            elif current_state == STATE_MENU:
                if key in (pygame.K_UP, pygame.K_DOWN):
                    menu_sel = 1 - menu_sel
                elif key == pygame.K_SPACE:
                    if menu_sel == 0:
                        error_msg = None; open_server_socket(); current_state = STATE_WAITING
                    else:
                        running = False

            elif current_state == STATE_MAP_SELECT:
                if key == pygame.K_UP:
                    map_sel = (map_sel - 1) % max(1, len(map_names))
                elif key == pygame.K_DOWN:
                    map_sel = (map_sel + 1) % max(1, len(map_names))
                elif key == pygame.K_SPACE and map_names:
                    send_msg(conn_sock, {'map': map_names[map_sel]})
                    start_race(map_names[map_sel])
                    current_state = STATE_GAME

            elif current_state == STATE_POST_RACE:
                if key in (pygame.K_LEFT, pygame.K_RIGHT):
                    post_sel = 1 - post_sel
                elif key == pygame.K_SPACE:
                    if post_sel == 0:
                        state_q.put({'replay': True})
                        map_names = scan_map_files(); map_row_rects = build_map_row_rects(len(map_names))
                        map_sel = 0; current_state = STATE_MAP_SELECT
                    else:
                        disconnect(None); error_msg = None; close_server()

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if current_state == STATE_MENU:
                if play_button_rect.collidepoint(pos):
                    error_msg = None; open_server_socket(); current_state = STATE_WAITING
                elif quit_button_rect.collidepoint(pos):
                    running = False
            elif current_state == STATE_MAP_SELECT:
                for i, rect in enumerate(map_row_rects):
                    if rect.collidepoint(pos):
                        send_msg(conn_sock, {'map': map_names[i]})
                        start_race(map_names[i])
                        current_state = STATE_GAME
                        break
            elif current_state == STATE_POST_RACE:
                if post_race_again_rect.collidepoint(pos):
                    state_q.put({'replay': True})
                    map_names = scan_map_files(); map_row_rects = build_map_row_rects(len(map_names))
                    map_sel = 0; current_state = STATE_MAP_SELECT
                elif post_race_menu_rect.collidepoint(pos):
                    disconnect(None); error_msg = None; close_server()

    if current_state == STATE_MENU:
        render_menu(screen, play_button_rect, quit_button_rect, title_font, button_font, error_msg, menu_sel)
        pygame.display.flip(); continue

    if current_state == STATE_WAITING:
        render_status_screen(screen, title_font, button_font, 'Waiting for player 2...')
        pygame.display.flip(); continue

    if current_state == STATE_MAP_SELECT:
        render_map_select(screen, map_names, map_row_rects, title_font, button_font, map_sel)
        pygame.display.flip(); continue

    if current_state == STATE_POST_RACE:
        screen.fill(COLOR_BACKGROUND)
        render_map(screen, map_surface, camera, full_viewport, overlay_vehicles=[p2])
        _draw_player_sprite_static(screen, full_viewport.centerx, full_viewport.centery, p1.color, p1.size_multiplier)
        render_hud(screen, full_viewport, p1.max_lap, TOTAL_LAPS,
                   p1_total_timer, p1_lap_timer, p1.finish_place, hud_font, place_font, p1.stored_powerup)
        render_post_race(screen, post_race_again_rect, post_race_menu_rect, title_font, button_font, post_sel)
        pygame.display.flip()
        state_q.put(build_state_packet(STATE_POST_RACE, 0.0, 0.0))
        continue

    # STATE: GAME
    if not DEBUG:
        motor_update()
    p2_collided   = False
    p2_shell_hit  = False

    if countdown_remaining > 0.0:
        countdown_remaining = max(0.0, countdown_remaining - dt)
        if countdown_remaining == 0.0:
            go_display_remaining = GO_DISPLAY_DURATION
    if go_display_remaining > 0.0:
        go_display_remaining = max(0.0, go_display_remaining - dt)

    track_record = False
    if countdown_remaining <= 0.0:
        if DEBUG:
            p1.handle_input()
            if pygame.key.get_pressed()[pygame.K_SPACE] and not p1.race_finished:
                activate_consumable(p1, p2, shells, owner_index=0)
        else:
            p1.throttle_input = -joystick_throttle()
            p1.steer_input    = joystick_steer()
            if joystick_btn(JS_SW) and not p1.race_finished:
                activate_consumable(p1, p2, shells, owner_index=0)
                motor_rumble()

        p2.throttle_input = float(last_client_input.get('throttle', 0))
        p2.steer_input    = float(last_client_input.get('steer', 0))
        p2_act = bool(last_client_input.get('activate', False))
        if p2_act and not p2_prev_activate and not p2.race_finished:
            activate_consumable(p2, p1, shells, owner_index=1)
        p2_prev_activate = p2_act

        p1.update_physics(dt)
        p2.update_physics(dt)
        apply_passive_tile_effects(p1, game_map)
        apply_passive_tile_effects(p2, game_map)

        for sp in spawners:
            sp.update(dt)
            sp.try_collect(p1)
            sp.try_collect(p2)

        surviving = []
        for sh in shells:
            target = p2 if sh.owner_index == 0 else p1
            sh.update(dt, target.world_x, target.world_y)
            wall_normal = sh.hits_wall(game_map, tri_masks, player_mask)
            if wall_normal is not None:
                if sh.bounces_remaining <= 0:
                    continue
                nx, ny = wall_normal
                vn = sh.vel_x * nx + sh.vel_y * ny
                sh.vel_x -= 2.0 * vn * nx
                sh.vel_y -= 2.0 * vn * ny
                sh.heading = math.atan2(sh.vel_y, sh.vel_x)
                sh.bounces_remaining -= 1
            hit = p2 if sh.owner_index == 0 else p1
            if sh.hits_vehicle(hit):
                sh.bounces_remaining -= 1
                nx, ny   = math.cos(sh.heading), math.sin(sh.heading)
                vn_hit   = hit.vel_x * nx + hit.vel_y * ny
                vn_shell = sh.vel_x  * nx + sh.vel_y  * ny
                hit.vel_x += (vn_shell - vn_hit) * nx
                hit.vel_y += (vn_shell - vn_hit) * ny
                sh.vel_x -= 2.0 * vn_shell * nx
                sh.vel_y -= 2.0 * vn_shell * ny
                sh.heading = math.atan2(sh.vel_y, sh.vel_x)
                if hit is p1 and not DEBUG:
                    motor_rumble()
                if hit is p2:
                    p2_shell_hit = True
                if sh.bounces_remaining <= 0:
                    continue
            surviving.append(sh)
        shells = surviving

        # out-of-bounds correction: snap exterior vehicles/shells to nearest interior tile
        for v in (p1, p2):
            if not game_map.is_interior(v.world_x, v.world_y):
                v.world_x, v.world_y = game_map.nearest_interior_center(v.world_x, v.world_y)
                v.vel_x = v.vel_y = 0.0
        shells = [sh for sh in shells if game_map.is_interior(sh.world_x, sh.world_y)]

        for p, fin in ((p1, update_lap_progress(p1, game_map)),
                       (p2, update_lap_progress(p2, game_map))):
            if fin:
                p.finish_place = next_finish_place
                next_finish_place += 1

        if not p1.race_finished:
            if p1.max_lap > p1_prev_lap:
                p1_lap_timer = 0.0
                p1_prev_lap  = p1.max_lap
            p1_total_timer += dt
            p1_lap_timer   += dt

        if p1.resolve_collisions(game_map, tri_masks, player_mask) and not DEBUG:
            motor_rumble()
        p2_wall_hit = p2.resolve_collisions(game_map, tri_masks, player_mask)
        vehicle_hit = resolve_vehicle_collision(p1, p2)
        if vehicle_hit:
            p2_collided = True
            if not DEBUG:
                motor_rumble()
        if p2_wall_hit:
            p2_collided = True

        if p1.race_finished and p2.race_finished:
            current_state = STATE_POST_RACE

        track_record = False
        p1.track_frame_counter += 1
        if p1.track_frame_counter >= TRACK_HISTORY_INTERVAL:
            p1.track_frame_counter = 0
            p1.track_history.append((p1.world_x, p1.world_y))
            track_record = True
        p2.track_frame_counter += 1
        if p2.track_frame_counter >= TRACK_HISTORY_INTERVAL:
            p2.track_frame_counter = 0
            p2.track_history.append((p2.world_x, p2.world_y))
            track_record = True

    camera.center_on(p1.world_x, p1.world_y)
    camera.heading = p1.heading

    screen.fill(COLOR_BACKGROUND)
    render_map(screen, map_surface, camera, full_viewport,
               overlay_vehicles=[p2], overlay_shells=shells, overlay_spawners=spawners,
               track_histories=[(p1.track_history, COLOR_TRACK_P1), (p2.track_history, COLOR_TRACK_P2)])
    _draw_player_sprite_static(screen, full_viewport.centerx, full_viewport.centery, p1.color, p1.size_multiplier)
    render_hud(screen, full_viewport, p1.max_lap, TOTAL_LAPS,
               p1_total_timer, p1_lap_timer, p1.finish_place, hud_font, place_font, p1.stored_powerup)

    if countdown_remaining > 0.0:
        render_center_overlay_message(screen, str(int(math.ceil(countdown_remaining))), countdown_font)
    elif go_display_remaining > 0.0:
        render_center_overlay_message(screen, 'GO!', countdown_font)

    pygame.display.flip()
    state_q.put(build_state_packet(STATE_GAME, countdown_remaining, go_display_remaining, track_record))

pygame.quit()
log("shutting down")
close_server()
if not DEBUG:
    joystick_stop()
    motor_stop()
sys.exit()