#!/bin/bash
# git wrapper using alpine/git Docker image (NAS doesn't have git natively).
DOCKER=/var/packages/ContainerManager/target/usr/bin/docker
SSH_DIR=/var/services/homes/artemere-7601341/.ssh
REPO=/volume1/homes/artemere-7601341/scripts
exec $DOCKER run --rm   -v $REPO:/repo   -v $SSH_DIR:/root/.ssh:ro   -e GIT_AUTHOR_NAME='Artem Eremenko'   -e GIT_AUTHOR_EMAIL='artem.eremenko@gmail.com'   -e GIT_COMMITTER_NAME='Artem Eremenko'   -e GIT_COMMITTER_EMAIL='artem.eremenko@gmail.com'   -e GIT_SSH_COMMAND='ssh -i /root/.ssh/github_ed25519 -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes'   -w /repo   alpine/git   -c safe.directory=/repo   -c init.defaultBranch=main   -c core.fileMode=false   "$@"
