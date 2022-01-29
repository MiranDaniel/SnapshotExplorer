from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import re
import requests
import json
import pandas as pd
import markdown as md
from lxml import etree
from datetime import datetime
import pickle
import time

app = Flask(__name__)
url = 'https://hub.snapshot.org/graphql'

CLEANR = re.compile(r'<(?!\/?(a|br)(?=>|\s.*>))\/?.*?>')


def save_pickle():
    pickle.dump(masterCache, open("masterCache.p", "wb"))
    pickle.dump(balanceCache, open("balanceCache.p", "wb"))


try:
    masterCache = pickle.load(open("masterCache.p", "rb"))
except FileNotFoundError:
    masterCache = {}
    save_pickle()

try:
    balanceCache = pickle.load(open("balanceCache.p", "rb"))
except FileNotFoundError:
    balanceCache = {}
    save_pickle()

leaderboard = {}


def clean_html(raw_html):
    cleantext = re.sub(CLEANR, '', raw_html)
    return cleantext


def resolve_ens(ens):
    print(f"Resolving {ens}")
    ens = ens.replace(".eth", "")
    if ens in masterCache:
        print(f"Found {ens}.eth in cache")
        return masterCache[ens]
    query = """
        {
        domains(first: 5 where: {labelName: "%s"}) {
            resolvedAddress{
                id
            }
        }
        }
    """ % ens
    r = requests.post(
        "https://api.thegraph.com/subgraphs/name/ensdomains/ens", json={'query': query})

    try:
        masterCache[ens] = r.json(
        )["data"]["domains"][0]["resolvedAddress"]["id"]
    except IndexError:
        return None
    else:
        save_pickle()

    return r.json()["data"]["domains"][0]["resolvedAddress"]["id"]


def get_proposals(address):
    query = """
    query {
    proposals(
        first: 20000000,
        skip: 0,
        where: {
        author: "%s"
        }
    ) {
        id
        title
        body
        choices
        start
        end
        snapshot
        state
        scores_by_strategy
        space {
        id
        name
        }
    }
    }
    """ % address

    r = requests.post(url, json={'query': query})
    data = []
    rj = json.loads(r.text)
    for i in range(len(rj['data']['proposals'])):
        cache = rj['data']['proposals'][i]

        cache["created"] = cache["start"]

        cache["start"] = datetime.fromtimestamp(
            cache["start"]).strftime('%Y-%m-%d %H:%M:%S')
        cache["end"] = datetime.fromtimestamp(
            cache["end"]).strftime('%Y-%m-%d %H:%M:%S')

        cache["type"] = "proposal"

        total = sum([x[0] for x in cache["scores_by_strategy"]])
        for i in range(len(cache["scores_by_strategy"])):
            if total == 0:
                cache["scores_by_strategy"][i][0] = 0
                continue
            cache["scores_by_strategy"][i][0] = cache["scores_by_strategy"][i][0] / total*100

        html = md.markdown(cache["body"]).replace("\n", "<br>")
        cache["body"] = clean_html(html)
        body_ = cache["body"].split("<br>")
        cache["title_"] = "Click here to read full proposal text"
        data.append(cache)

    return data


def get_votes(address, noCache=False):
    query = """
    query
    votes {
    votes(
        first: 100000
        where: {
        voter: "%s"
        }
    ) {
        id
        voter
        created
        proposal {
            title
            choices
            start
            end
            state
            body
            scores_by_strategy
        }
        choice
        vp
        space {
            id
            name
            symbol
        }
    }
    }
    """ % address
    r = requests.post(url, json={'query': query})
    data = []
    rj = json.loads(r.text)
    majority = []
    for i in range(len(rj["data"]["votes"])):
        print(f"Loading vote {i}")
        cache = rj["data"]["votes"][i]

        cache["type"] = "vote"

        if cache["proposal"] is None:
            continue

        cache["proposal"]["start"] = datetime.fromtimestamp(
            cache["proposal"]["start"]).strftime('%Y-%m-%d %H:%M:%S')
        cache["proposal"]["end"] = datetime.fromtimestamp(
            cache["proposal"]["end"]).strftime('%Y-%m-%d %H:%M:%S')

        if type(cache["choice"]) == int:
            cache["choice"] = [cache["choice"]]

        html = md.markdown(cache["proposal"]["body"]).replace("\n", "<br>")

        cache["vp"] = round(cache["vp"], 3)

        cache["body"] = clean_html(html)
        body_ = cache["body"].split("<br>")
        cache["title_"] = "Click here to read full proposal text"

        total = 0
        for i in range(len(cache["proposal"]["scores_by_strategy"])):
            for j in cache["proposal"]["scores_by_strategy"][i]:
                if type(j) not in [float, int]:
                    continue
                total += j

        alignment = []
        for i in cache["choice"]:
            index = int(i)-1
            if total == 0:
                continue
            alignment.append(
                cache["proposal"]["scores_by_strategy"][index][0] / total*100)

        majority.append(sum(alignment)/len(alignment))
        cache["majority"] = alignment

        data.append(cache)

    if len(majority) > 0:
        majority = round(sum(majority)/len(majority), 2)
    else:
        majority = None
    print(f"Majority: {majority}")
    return data, majority


@app.route("/", methods=['GET', 'POST'])
def hello_world():
    global leaderboard
    print(request.form.get("address"))
    leaderboard = dict(sorted(leaderboard.items(), key=lambda item: item[1]))
    return render_template('index.html', leaderboard={k: leaderboard[k] for k in list(leaderboard)[:10]})


@app.route("/address/<address>/", methods=['GET', 'POST'])
@app.route("/address/", methods=['GET', 'POST'])
def account(address=None):
    if address == None:
        return redirect(f"address/{request.form.get('address')}/")

    r = re.compile(r"(^0x[a-fA-F0-9]{40}$|\w{3,256}.eth)")
    r = r.match(address)

    if r == None:
        return render_template('address.html', error="Error -1", message="Invalid address")

    if ".eth" in address:
        ens = address
        address = resolve_ens(address)
        if address == None:
            return render_template('address.html', error="Error -2", message="ENS address not found")
    else:
        ens = None

    if address in leaderboard:
        if ens == None:
            leaderboard[address] += 1
        else:
            leaderboard[ens] += 1
    else:
        if ens == None:
            leaderboard[address] = 1
        else:
            leaderboard[ens] = 1

    if request.args.get('noCache') != "True":
        if address in balanceCache:
            # only accept newer than 5 minutes
            if balanceCache[address][0] > (time.time() - 300):
                x = balanceCache[address]
                if ens != None:
                    address = ens
                return render_template('address.html', cache=True, address=address, activity=x[1], proposal_count=x[2], vote_count=x[3], len=len, majority=x[4])

    print("Getting proposals")
    activity = get_proposals(address)
    print("Getting votes")

    x = get_votes(address, bool(request.args.get('noCache')))
    majority = x[1]
    for i in x[0]:
        activity.append(i)
    pc = 0
    vc = 0
    for i in activity:
        if i["type"] == "proposal":
            pc += 1
        elif i["type"] == "vote":
            vc += 1

    activity = sorted(activity, key=lambda i: i['created'], reverse=True)

    balanceCache[address] = (time.time(), activity, pc, vc, majority)

    save_pickle()

    if ens != None:
        address = ens

    return render_template('address.html', cache=False, address=address, activity=activity, proposal_count=pc, vote_count=vc, len=len, majority=majority)


app.run(port=8080)
