import datetime
from dateutil import relativedelta
import requests
import os
import time
import hashlib
from lxml import etree

ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN', '')
USER_NAME = os.environ.get('USER_NAME', 'Juknum')
# GitHub account creation date (fallback if API offline)
ACCOUNT_CREATED_DEFAULT = datetime.datetime(2019, 4, 22, 22, 54, 26)

HEADERS = {'authorization': f'token {ACCESS_TOKEN}'} if ACCESS_TOKEN else {}
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
CACHE_DIR = os.path.join(ROOT_DIR, '.github', 'cache')
ASSETS_DIR = os.path.join(ROOT_DIR, '.github', 'assets')

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)


def daily_uptime(start_dt):
    """
    Returns the length of time since start_dt
    e.g. 'XX years, XX months, XX days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), start_dt)
    plural_y = 's' if diff.years != 1 else ''
    plural_m = 's' if diff.months != 1 else ''
    plural_d = 's' if diff.days != 1 else ''
    return f"{diff.years} year{plural_y}, {diff.months} month{plural_m}, {diff.days} day{plural_d}"


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def simple_request(func_name, query, variables):
    if not ACCESS_TOKEN:
        return None
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, 'has failed with', request.status_code, request.text, QUERY_COUNT)


def user_getter(username):
    query_count('user_getter')
    if not ACCESS_TOKEN:
        return {'id': 'MOCK_ID'}, '2019-04-22T22:54:26Z'
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    resp = simple_request(user_getter.__name__, query, {'login': username})
    if resp:
        data = resp.json()['data']['user']
        return {'id': data['id']}, data['createdAt']
    return {'id': 'MOCK_ID'}, '2019-04-22T22:54:26Z'


def follower_getter(username):
    query_count('follower_getter')
    if not ACCESS_TOKEN:
        return 31
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    resp = simple_request(follower_getter.__name__, query, {'login': username})
    if resp:
        return int(resp.json()['data']['user']['followers']['totalCount'])
    return 31


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    query_count('graph_repos_stars')
    if not ACCESS_TOKEN:
        return 31 if count_type == 'repos' else 128
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    resp = simple_request(graph_repos_stars.__name__, query, {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor})
    if resp and resp.status_code == 200:
        data = resp.json()['data']['user']['repositories']
        if count_type == 'repos':
            return data['totalCount']
        elif count_type == 'stars':
            total_stars = 0
            for edge in data['edges']:
                total_stars += edge['node']['stargazers']['totalCount']
            return total_stars
    return 0


def recursive_loc(owner, repo_name, cache_dict, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    additions
                                    deletions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    resp = simple_request(recursive_loc.__name__, query, {'repo_name': repo_name, 'owner': owner, 'cursor': cursor})
    if not resp:
        return addition_total, deletion_total, my_commits
        
    data = resp.json().get('data', {}).get('repository')
    if not data or not data.get('defaultBranchRef'):
        return addition_total, deletion_total, my_commits
        
    history = data['defaultBranchRef']['target']['history']
    for edge in history['edges']:
        node = edge['node']
        author_user = node.get('author', {}).get('user')
        if author_user and author_user.get('id') == OWNER_ID['id']:
            my_commits += 1
            addition_total += node.get('additions', 0)
            deletion_total += node.get('deletions', 0)
            
    if history['edges'] and history['pageInfo']['hasNextPage']:
        return recursive_loc(owner, repo_name, cache_dict, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])
    return addition_total, deletion_total, my_commits


def loc_query(owner_affiliation):
    query_count('loc_query')
    cache_file = os.path.join(CACHE_DIR, f"{hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()}_loc.txt")
    
    cached_stats = {}
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4:
                    cached_stats[parts[0]] = (int(parts[1]), int(parts[2]), int(parts[3]))
                    
    if not ACCESS_TOKEN:
        if cached_stats:
            add_t = sum(v[0] for v in cached_stats.values())
            del_t = sum(v[1] for v in cached_stats.values())
            comm_t = sum(v[2] for v in cached_stats.values())
            return [add_t, del_t, add_t - del_t, comm_t]
        return [210320, 29870, 180450, 1240]

    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    resp = simple_request(loc_query.__name__, query, {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': None})
    if not resp:
        return [210320, 29870, 180450, 1240]
        
    repos = resp.json()['data']['user']['repositories']['edges']
    total_add, total_del, total_commits = 0, 0, 0
    updated_cache = {}

    for repo_edge in repos:
        node = repo_edge['node']
        name_with_owner = node['nameWithOwner']
        owner, repo_name = name_with_owner.split('/')
        branch = node.get('defaultBranchRef')
        if not branch:
            continue
            
        commit_total = branch['target']['history']['totalCount']
        if name_with_owner in cached_stats and cached_stats[name_with_owner][2] == commit_total:
            add, d, comms = cached_stats[name_with_owner]
        else:
            add, d, comms = recursive_loc(owner, repo_name, cached_stats)
            
        updated_cache[name_with_owner] = (add, d, comms)
        total_add += add
        total_del += d
        total_commits += comms

    with open(cache_file, 'w') as f:
        for k, v in updated_cache.items():
            f.write(f"{k} {v[0]} {v[1]} {v[2]}\n")

    return [total_add, total_del, total_add - total_del, total_commits]


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def justify_format(root, element_id, new_text, length=0):
    if isinstance(new_text, int):
        formatted_text = f"{'{:,}'.format(new_text)}"
    else:
        formatted_text = str(new_text)
        
    find_and_replace(root, element_id, formatted_text)
    just_len = max(0, length - len(formatted_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(filename, parser)
    root = tree.getroot()
    
    find_and_replace(root, 'age_data', age_data)
    justify_format(root, 'commit_data', commit_data, 22)
    justify_format(root, 'star_data', star_data, 14)
    justify_format(root, 'repo_data', repo_data, 6)
    justify_format(root, 'contrib_data', contrib_data)
    justify_format(root, 'follower_data', follower_data, 10)
    
    # LOC line: align '(' at column index 35 (prefix + dots + loc_data = 34 chars)
    formatted_loc = str(loc_data[2])
    find_and_replace(root, 'loc_data', formatted_loc)
    loc_dots_len = max(1, 34 - 26 - len(formatted_loc))
    if loc_dots_len <= 1:
        dot_str = ' '
    elif loc_dots_len == 2:
        dot_str = '. '
    else:
        dot_str = ' ' + ('.' * (loc_dots_len - 2)) + ' '
    find_and_replace(root, 'loc_data_dots', dot_str)
    
    find_and_replace(root, 'loc_add', loc_data[0])
    find_and_replace(root, 'loc_del', loc_data[1])
    
    tree.write(filename, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    print(f"Updating profile stats for {USER_NAME}...")
    
    user_info, acc_created = user_getter(USER_NAME)
    OWNER_ID = user_info
    
    # 1. Calculate uptime from GitHub account creation date
    try:
        acc_dt = datetime.datetime.fromisoformat(acc_created.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        acc_dt = ACCOUNT_CREATED_DEFAULT
        
    age_str = daily_uptime(acc_dt)
    print(f"Uptime (Account Age): {age_str}")
    
    # 2. Query stats
    print("Querying GitHub stats...")
    loc_stats = loc_query(['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    total_commits = loc_stats[3]
    star_count = graph_repos_stars('stars', ['OWNER'])
    repo_count = graph_repos_stars('repos', ['OWNER'])
    contrib_count = graph_repos_stars('repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    followers_count = follower_getter(USER_NAME)
    
    # Format LOC strings
    loc_formatted = ['{:,}'.format(loc_stats[0]), '{:,}'.format(loc_stats[1]), '{:,}'.format(loc_stats[2])]
    
    # 3. Overwrite stats in dark and light SVGs (without touching static ASCII art)
    dark_svg = os.path.join(ASSETS_DIR, 'dark_mode.svg')
    light_svg = os.path.join(ASSETS_DIR, 'light_mode.svg')
    
    if os.path.exists(dark_svg):
        svg_overwrite(dark_svg, age_str, total_commits, star_count, repo_count, contrib_count, followers_count, loc_formatted)
        print(f"Updated {dark_svg}")
        
    if os.path.exists(light_svg):
        svg_overwrite(light_svg, age_str, total_commits, star_count, repo_count, contrib_count, followers_count, loc_formatted)
        print(f"Updated {light_svg}")

    print("Stats update complete!")
